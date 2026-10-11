"""Render release installation notes from admitted native and npm identities."""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path
import re
import tomllib

REPOSITORY = "https://github.com/kochetkov-ma/brewdoc"


def read_json(path: Path) -> dict:
    """Require an object at the declared evidence path."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Guide evidence must be an object: {path.name}")
    return value


def project_version(checkout: Path) -> str:
    """Refuse version drift between package metadata and public Python exports."""
    version = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    module = ast.parse((checkout / "src/brewdoc/__init__.py").read_text(encoding="utf-8"))
    exported = [ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)]
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version) or exported != [version]:
        raise ValueError("Guide project/API version identity differs")
    return version


def check_asset(folder: Path, record: dict, expected_name: str) -> None:
    """Bind a fixed non-escaping asset name to its actual accepted bytes."""
    if (record["filename"] != expected_name or Path(expected_name).name != expected_name
            or type(record["bytes"]) is not int or record["bytes"] <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])):
        raise ValueError("Guide archive identity differs")
    path = folder / expected_name
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if (path.stat().st_size, actual) != (record["bytes"], record["sha256"]):
        raise ValueError("Guide archive bytes differ")


def admit(checkout: Path, native_path: Path, npm_path: Path, phase: str,
          availability_path: Path | None = None) -> dict:
    """Refuse artifact drift and require matched public bytes and consumers for public notes."""
    version = project_version(checkout)
    policy = read_json(checkout / ".github/distribution.json")
    native, npm = read_json(native_path), read_json(npm_path)
    source = native.get("source_sha", "")
    if (policy["schema"] != 1 or native.get("schema") != 1 or native.get("version") != version
            or not re.fullmatch(r"[0-9a-f]{40}", source)):
        raise ValueError("Guide native release identity differs")
    if set(native["targets"]) != set(policy["targets"]):
        raise ValueError("Guide native target set differs")
    checksums = []
    for target, target_policy in policy["targets"].items():
        record = native["targets"][target]
        if (record["schema"], record["version"], record["source_sha"], record["python"], record["target"]) != (
                1, version, source, policy["python"], target):
            raise ValueError("Guide target identity differs")
        if record["signing"] != policy["signing"] or record["minimum_tested"] != target_policy["verification_floor"]:
            raise ValueError("Guide target runtime or signing identity differs")
        archive = record["archive"]
        check_asset(native_path.parent, archive, f"brewdoc-{version}-{target}.{target_policy['archive']}")
        checksums.append(f"{archive['sha256']}  {archive['filename']}")
    checksums.sort()
    release_manifest_path = native_path.parent / "release-manifest.json"
    release_assets = None
    if release_manifest_path.exists():
        release_manifest = read_json(release_manifest_path)
        if (release_manifest["schema"], release_manifest["version"], release_manifest["source_sha"],
                release_manifest["tag"]) != (1, version, source, f"v{version}"):
            raise ValueError("Guide release archive identity differs")
        release_assets = release_manifest["assets"]
        for name, asset in release_assets.items():
            if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]):
                raise ValueError("Guide release checksum identity differs")
        for record in native["targets"].values():
            archive = record["archive"]
            if release_assets.get(archive["filename"]) != {key: archive[key] for key in ("bytes", "sha256")}:
                raise ValueError("Guide release archive identity differs")
        checksums = [f"{asset['sha256']}  {name}" for name, asset in sorted(release_assets.items())]
    if (native_path.parent / "SHA256SUMS").read_text(encoding="ascii") != "\n".join(checksums) + "\n":
        raise ValueError("Guide native checksum inventory differs")
    npm_policy = policy["npm"]
    if (npm.get("schema"), npm.get("version"), npm.get("source_sha"), npm.get("node"), npm.get("npm")) != (
            1, version, source, npm_policy["node"], npm_policy["npm"]):
        raise ValueError("Guide npm release identity differs")
    packages = npm["packages"]
    expected_names = [*npm_policy["packages"].values(), npm_policy["name"]]
    if [record["metadata"]["name"] for record in packages] != expected_names:
        raise ValueError("Guide npm package identity differs")
    for target, record in zip([*npm_policy["packages"], None], packages, strict=True):
        if (record["version"], record["source_sha"], record["metadata"]["version"]) != (version, source, version):
            raise ValueError("Guide npm package version/source identity differs")
        name = record["metadata"]["name"]
        check_asset(npm_path.parent, record, f"{name}-{version}.tgz")
        if release_assets is not None and release_assets.get(record["filename"]) != {
                key: record[key] for key in ("bytes", "sha256")}:
            raise ValueError("Guide npm release archive identity differs")
        with (npm_path.parent / record["filename"]).open("rb") as stream:
            integrity = "sha512-" + base64.b64encode(hashlib.file_digest(stream, "sha512").digest()).decode("ascii")
        if record["integrity"] != integrity:
            raise ValueError("Guide npm integrity identity differs")
        if target is not None and (record.get("target") != target
                                   or record["native_archive"] != native["targets"][target]["archive"]):
            raise ValueError("Guide npm native target identity differs")
    main = packages[-1]["metadata"]
    if (main["optionalDependencies"] != {name: version for name in npm_policy["packages"].values()}
            or {target: facts["package"] for target, facts in main["brewdocTargets"].items()} != npm_policy["packages"]):
        raise ValueError("Guide npm optional package identity differs")
    if phase not in {"staged", "public"}:
        raise ValueError("Guide publication phase refused")
    if phase == "public":
        if availability_path is None:
            raise ValueError("Guide public availability evidence is missing")
        availability = read_json(availability_path)
        expected_assets = {record["archive"]["filename"]: {
            key: record["archive"][key] for key in ("bytes", "sha256")} for record in native["targets"].values()}
        expected_packages = {record["metadata"]["name"]: {
            "version": version, "integrity": record["integrity"]} for record in packages}
        expected_consumers = {target: {"native": "PASS", "npm": "PASS", "npx": "PASS"} for target in policy["targets"]}
        if (availability.get("schema"), availability.get("version"), availability.get("source_sha"),
                availability.get("pypi"), availability.get("github"), availability.get("npm"), availability.get("consumers")) != (
                1, version, source, {"published": True}, {"assets": expected_assets},
                {"packages": expected_packages}, expected_consumers):
            raise ValueError("Guide public bytes or consumer acceptance differs")
    return {"version": version, "source_sha": source, "phase": phase, "policy": policy,
            "targets": {target: native["targets"][target] for target in policy["targets"]}, "npm": npm}


def python_install(version: str) -> str:
    """Give independent Python install, version, conversion and removal flows."""
    return f'''### pip

Requires an installed Python 3.12 or newer. HTML/URL require CPython 3.12-3.14.
Use a dedicated environment. These commands need no activation or PATH change.

Linux/macOS:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install "brewdoc=={version}"
.venv/bin/python -c "from importlib.metadata import version; print(version('brewdoc'))"
.venv/bin/brewdoc --self-check
.venv/bin/brewdoc file.pdf --out file.md
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install "brewdoc=={version}"
.\\.venv\\Scripts\\python.exe -c "from importlib.metadata import version; print(version('brewdoc'))"
.\\.venv\\Scripts\\brewdoc.exe --self-check
.\\.venv\\Scripts\\brewdoc.exe file.pdf --out file.md
```

To update an older environment to this release, use its explicit Python path:

```sh
.venv/bin/python -m pip install --upgrade "brewdoc=={version}"
.venv/bin/brewdoc --self-check
```

Windows PowerShell:

```powershell
.\\.venv\\Scripts\\python.exe -m pip install --upgrade "brewdoc=={version}"
.\\.venv\\Scripts\\brewdoc.exe --self-check
```

To remove, run `.venv/bin/python -m pip uninstall brewdoc` on Unix or
`.\\.venv\\Scripts\\python.exe -m pip uninstall brewdoc` in PowerShell.
Dependencies remain; remove the dedicated .venv directory when no longer needed.

### uv

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/). uv can obtain
Python when needed. Temporary runs use their own cached environment:

```sh
uvx --python 3.12 --from "brewdoc=={version}" brewdoc --self-check
uvx --python 3.12 --from "brewdoc=={version}" brewdoc file.pdf --out file.md
```

Temporary uvx runs add no persistent command to PATH. Choose a different exact
published version on the next invocation to update; there is no tool to uninstall.
`uv cache clean brewdoc` removes cached package data, not a managed tool.

For a persistent command:

```sh
uv tool install --python 3.12 "brewdoc=={version}"
uv tool update-shell
```

Open a new terminal, then verify and convert:

```sh
uv tool list
brewdoc --self-check
brewdoc file.pdf --out file.md
```

`uv tool list` shows the installed version. Use `command -v brewdoc` on Unix or
`Get-Command brewdoc` in PowerShell to check the command selected by PATH.
To update an older tool to this release:

```sh
uv tool install --force --python 3.12 "brewdoc=={version}"
brewdoc --self-check
```

Remove with `uv tool uninstall brewdoc`.

### pipx

Requires [pipx](https://pipx.pypa.io/stable/how-to/install-pipx.html) and Python 3.12 installed.

```sh
pipx install --python 3.12 "brewdoc=={version}"
pipx ensurepath
```

Open a new terminal, then verify and convert:

```sh
pipx list
brewdoc --self-check
brewdoc file.pdf --out file.md
```

`pipx list` shows the installed version; check the selected executable with
`command -v brewdoc` or PowerShell `Get-Command brewdoc`.
To update an older application to this release:

```sh
pipx install --force --python 3.12 "brewdoc=={version}"
brewdoc --self-check
```

Remove with `pipx uninstall brewdoc`. For a later release, select an exact published
version and read its own prerequisites and guide.
'''


def native_install(facts: dict) -> str:
    """Keep checksums, complete bundle directories and actual runtime limits visible."""
    version, policy = facts["version"], facts["policy"]
    base = f"{REPOSITORY}/releases/download/v{version}"
    rows = ["### Standalone downloads", "", "Python is bundled. Keep the whole extracted brewdoc directory.", "",
            "| Target | Download | Verified target | JavaScript |", "|---|---|---|---|"]
    for target, record in facts["targets"].items():
        archive = record["archive"]
        js = "Included" if policy["targets"][target]["render_js"] else "Refused before requests"
        rows.append(f"| {target} | [{archive['filename']}]({base}/{archive['filename']}) | {record['minimum_tested']} | {js} |")
    rows.extend(["", f"Download [SHA256SUMS]({base}/SHA256SUMS) beside your selected archive.",
                 "Checksums detect changed bytes; they are not detached publisher signatures.", ""])
    for target in ("linux-x64", "macos-arm64"):
        archive = facts["targets"][target]["archive"]["filename"]
        checker = "sha256sum" if target == "linux-x64" else "shasum -a 256"
        rows.extend([f"{target}:", "", "```sh", f'curl -fLO "{base}/{archive}"',
                     f'curl -fLO "{base}/SHA256SUMS"', f"{checker} {archive}",
                     f"awk '$2 == \"{archive}\" {{ print }}' SHA256SUMS", "```", "",
                     "Continue only when the two SHA-256 values match.", "", "```sh",
                     f"mkdir brewdoc-{version}-{target}", f"tar -xzf {archive} -C brewdoc-{version}-{target}",
                     f'cd "brewdoc-{version}-{target}"', "cat brewdoc/version.json",
                     "./brewdoc/brewdoc --self-check", "./brewdoc/brewdoc /absolute/path/file.pdf --out file.md",
                     'export PATH="$PWD/brewdoc:$PATH"', "```", ""])
    archive = facts["targets"]["windows-x64"]["archive"]["filename"]
    rows.extend(["Windows PowerShell:", "", "```powershell",
                 f'Invoke-WebRequest "{base}/{archive}" -OutFile "{archive}"',
                 f'Invoke-WebRequest "{base}/SHA256SUMS" -OutFile SHA256SUMS',
                 f'(Get-FileHash "{archive}" -Algorithm SHA256).Hash.ToLower()',
                 f'Select-String -Path SHA256SUMS -SimpleMatch "  {archive}"', "```", "",
                 "Continue only when the SHA-256 values match.", "", "```powershell",
                 f'Expand-Archive "{archive}" -DestinationPath "brewdoc-{version}-windows-x64"',
                 f'Set-Location "brewdoc-{version}-windows-x64"',
                 "Get-Content .\\brewdoc\\version.json", ".\\brewdoc\\brewdoc.exe --self-check",
                 '.\\brewdoc\\brewdoc.exe "C:\\path\\file.pdf" --out file.md',
                 '$env:Path = "$PWD\\brewdoc;$env:Path"', "```", "",
                 "The manifest supplies the bundled version; Brewdoc has no version flag.",
                 "The PATH changes above last for the current terminal. For persistence, add the",
                 "absolute brewdoc directory to your shell profile or Windows user PATH, then open",
                 "a new terminal. Check selection with command -v brewdoc or Get-Command brewdoc.", "",
                 "To update, download and verify another exact published release, extract a fresh",
                 "complete directory, self-check it and repoint PATH. Remove the old directory only",
                 "after verification. To uninstall, remove its dedicated directory and PATH entry.",
                 "Keep _internal and licenses beside the executable. Do not merge bundle versions.", "",
                 "Signing:", "", *[f"- {platform}: {status}." for platform, status in policy["signing"].items()], "",
                 "OS publisher/security policy may prevent launching these builds. No security",
                 "override is required or suggested by these instructions.", ""])
    return "\n".join(rows)


def render(facts: dict) -> str:
    """Produce one complete release body without publishing or acquiring anything."""
    version = facts["version"]
    condition = ("This release is staged. The commands and download links below apply only after "
                 "this exact version and channel are published and verified.\n\n" if facts["phase"] == "staged" else "")
    features = "\n".join(f"- {feature}." for feature in facts["policy"]["release_features"])
    return f'''## brewdoc {version}

{features}

[Python wheel]({REPOSITORY}/releases/download/v{version}/brewdoc-{version}-py3-none-any.whl) |
[Source distribution]({REPOSITORY}/releases/download/v{version}/brewdoc-{version}.tar.gz)

{condition}Use your own file.pdf. Conversion prints one JSON receipt line and writes Markdown
to --out. Without --out, Markdown follows the receipt on stdout. Local conversion
and self-check are offline. Exit 0 confirms conversion, 1 refusal and 2 usage error.

{python_install(version)}
### Optional URL JavaScript capture

Python installs use "brewdoc[render-js]=={version}" instead of the base package.
JS requires CPython 3.12-3.14 on macOS ARM64 or 64-bit glibc Linux; unsupported
runtimes refuse before requests. Standalone/npm bundles include their admitted
JS components on Linux x64 and macOS ARM64; Windows refuses JS.

```sh
brewdoc --url https://example.com --render-js --timeout 30 --out page.md
```

The default waiting budget is 30 seconds. Components are installed/bundled already;
no browser or JS runtime is downloaded during invocation. URL acquisition uses
validated HTTP(S), ordinarily GET, plus the narrow credential-free HN Algolia
XHR POST exception. See [URL limits]({REPOSITORY}/blob/v{version}/FORMATS.md#url-acquisition).

### npm and npx

Requires Node {facts['npm']['node']} with npm {facts['npm']['npm']} (tested consumer
versions). No separate Python is required on the three admitted native targets.
Use the global package for a persistent command:

```sh
npm install --global brewdoc@{version}
npm list --global brewdoc --depth=0
brewdoc --self-check
brewdoc file.pdf --out file.md
```

Check command selection with command -v brewdoc or PowerShell Get-Command brewdoc.
Update an older tool to this release with `npm install --global brewdoc@{version}`.
Remove with `npm uninstall --global brewdoc`. Keep optional dependencies enabled;
unsupported targets or missing platform packages refuse explicitly.

For a temporary invocation:

```sh
npx --yes --package brewdoc@{version} brewdoc --self-check
npx --yes --package brewdoc@{version} brewdoc file.pdf --out file.md
```

npx uses npm's cache and does not create a persistent global tool. Choose another
exact version on the next invocation to update. There is no installed tool to
uninstall; avoid clearing unrelated npm cache data.

{native_install(facts)}
[Latest release]({REPOSITORY}/releases/latest) |
[Full usage]({REPOSITORY}/blob/v{version}/README.md) |
[License]({REPOSITORY}/blob/v{version}/LICENSE)
'''


def check_document(text: str, version: str, policy: dict) -> None:
    """Refuse stale versions, unsupported assets and a nonexistent CLI flag."""
    specs = re.findall(r"brewdoc(?:\[render-js\])?(?:==|@)(\d+\.\d+\.\d+)", text)
    downloads = re.findall(r"/releases/download/v(\d+\.\d+\.\d+)/([^\s)\"'`]+)", text)
    assets = {f"brewdoc-{version}-{target}.{facts['archive']}" for target, facts in policy["targets"].items()}
    assets.update({f"brewdoc-{version}-py3-none-any.whl", f"brewdoc-{version}.tar.gz",
                   "SHA256SUMS", "binaries.json", "npm-manifest.json", "release-manifest.json"})
    assets.update(f"{name}-{version}.tgz" for name in (*policy["npm"]["packages"].values(), policy["npm"]["name"]))
    if ("brewdoc --version" in text or any(found != version for found in specs)
            or any(tag != version or name not in assets for tag, name in downloads)):
        raise ValueError("Maintained guide flag, version or asset differs")


def main(argv=None) -> int:
    """Render staged/public notes or check maintained docs; refuse before output writes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("render", "check"))
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--phase", choices=("staged", "public"), default="staged")
    parser.add_argument("--native-manifest", type=Path)
    parser.add_argument("--npm-manifest", type=Path)
    parser.add_argument("--availability", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        version = project_version(args.checkout)
        policy = read_json(args.checkout / ".github/distribution.json")
        if args.mode == "check":
            for name in ("README.md", "llms.txt"):
                check_document((args.checkout / name).read_text(encoding="utf-8"), version, policy)
            return 0
        if args.native_manifest is None or args.npm_manifest is None or args.output is None:
            parser.error("render requires --native-manifest, --npm-manifest and --output")
        body = render(admit(args.checkout, args.native_manifest, args.npm_manifest, args.phase, args.availability))
        check_document(body, version, policy)
        args.output.write_text(body, encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Release guide refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
