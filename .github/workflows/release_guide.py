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
    if set(npm_policy["packages"]) != {"linux-x64", "macos-arm64"}:
        raise ValueError("Guide npm target set differs")
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
        expected_consumers = {target: {"native": "PASS"} for target in policy["targets"]}
        for target in npm_policy["packages"]:
            expected_consumers[target].update(npm="PASS", npx="PASS")
        if (availability.get("schema"), availability.get("version"), availability.get("source_sha"),
                availability.get("pypi"), availability.get("github"), availability.get("npm"), availability.get("consumers")) != (
                1, version, source, {"published": True}, {"assets": expected_assets},
                {"packages": expected_packages}, expected_consumers):
            raise ValueError("Guide public bytes or consumer acceptance differs")
    return {"version": version, "source_sha": source, "phase": phase, "policy": policy,
            "targets": {target: native["targets"][target] for target in policy["targets"]}, "npm": npm}


def render(facts: dict) -> str:
    """Render concise notes; registry commands require admitted public consumers."""
    version = facts["version"]
    policy = facts["policy"]
    for field in ("release_features", "release_fixes", "release_examples"):
        entries = policy[field] if field == "release_features" else policy.get(field, [])
        if not isinstance(entries, list) or any(
                not isinstance(entry, str) or not entry.strip() or "\n" in entry or "\r" in entry
                for entry in entries):
            raise ValueError(f"Guide release metadata differs: {field}")
    if "release_pr" in policy and (type(policy["release_pr"]) is not int or policy["release_pr"] <= 0):
        raise ValueError("Guide release metadata differs: release_pr")
    notes = []
    for title, field in (("Added", "release_features"), ("Fixed", "release_fixes")):
        entries = policy.get(field, [])
        if entries:
            bullets = "\n".join(f"- {entry}" + ("" if entry.endswith((".", "!", "?")) else ".")
                                for entry in entries)
            notes.append(f"### {title}\n\n{bullets}")
    if policy.get("release_examples"):
        notes.append("Examples:\n\n```sh\n" + "\n".join(policy["release_examples"]) + "\n```")
    if "release_pr" in policy:
        number = policy["release_pr"]
        notes.append(f"[PR #{number}]({REPOSITORY}/pull/{number})")
    notes = "\n\n".join(notes)
    base = f"{REPOSITORY}/releases/download/v{version}"
    condition = ("Staged release. Install commands and downloads apply after publication and verification.\n\n"
                 if facts["phase"] == "staged" else "")
    npm = ("npm/npx: Linux x64/glibc and macOS ARM64.\n\n"
           f"```sh\nnpm install --global brewdoc@{version}\n"
           f"npx --yes --package brewdoc@{version} brewdoc --self-check\n```\n\n"
           if facts["phase"] == "public" else "npm/npx availability awaits public verification.\n\n")
    labels = {"linux-x64": "Linux x64/glibc", "windows-x64": "Windows x64", "macos-arm64": "macOS ARM64"}
    downloads = " | ".join(
        f"[{labels[target]}]({base}/{record['archive']['filename']})" for target, record in facts["targets"].items())
    return f'''[Release notes](#release-notes) | [Installation](#installation) | [Downloads](#downloads) | [Documentation](#documentation)

<a name="release-notes"></a>
## Release notes

{notes}

---

<a name="installation"></a>
## Installation

{condition}Choose one Python method (Python 3.12+, pip in an active virtual environment):

```sh
python -m pip install "brewdoc=={version}"
uv tool install --python 3.12 "brewdoc=={version}"
pipx install --python 3.12 "brewdoc=={version}"
```

{npm}Homebrew availability awaits tap publication and consumer verification.

```sh
brewdoc --self-check
brewdoc file.pdf --out file.md
```

<a name="downloads"></a>
## Downloads

Standalone bundles include Python. Extract the archive and keep the complete directory. From the extraction directory, run `./brewdoc/brewdoc` (Windows: `.\\brewdoc\\brewdoc.exe`).

{downloads}

Verify [SHA256SUMS]({base}/SHA256SUMS).

<a name="documentation"></a>
## Documentation

[README]({REPOSITORY}/blob/v{version}/README.md) | [Installation and updates]({REPOSITORY}/blob/v{version}/INSTALL.md) | [Formats and limits]({REPOSITORY}/blob/v{version}/FORMATS.md)
'''


def render_homebrew(facts: dict) -> str:
    """Render only public Unix bundle identities, without a second dependency graph."""
    if facts["phase"] != "public" or set(facts["targets"]) != {"linux-x64", "windows-x64", "macos-arm64"}:
        raise ValueError("Homebrew requires admitted public native targets")
    version, source = facts["version"], facts["source_sha"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Homebrew release identity differs")
    hashes = {}
    for target in ("linux-x64", "macos-arm64"):
        record = facts["targets"][target]
        if ((record["version"], record["source_sha"], record["target"]) != (version, source, target)
                or record["archive"]["filename"] != f"brewdoc-{version}-{target}.tar.gz"
                or not re.fullmatch(r"[0-9a-f]{64}", record["archive"]["sha256"])):
            raise ValueError("Homebrew native archive identity differs")
        hashes[target] = record["archive"]["sha256"]
    return rf'''# Source: {source}
require "json"

class Brewdoc < Formula
  desc "Deterministic document-to-Markdown conversion"
  homepage "https://github.com/kochetkov-ma/brewdoc"
  version "{version}"
  license "MIT"

  on_macos do
    depends_on arch: :arm64
    depends_on macos: :sequoia
    preserve_rpath
    url "https://github.com/kochetkov-ma/brewdoc/releases/download/v{version}/brewdoc-{version}-macos-arm64.tar.gz"
    sha256 "{hashes['macos-arm64']}"
  end

  on_linux do
    depends_on arch: :x86_64
    url "https://github.com/kochetkov-ma/brewdoc/releases/download/v{version}/brewdoc-{version}-linux-x64.tar.gz"
    sha256 "{hashes['linux-x64']}"
  end

  skip_clean "libexec"

  def install
    supported = (OS.mac? && Hardware::CPU.arm? && Hardware::CPU.is_64_bit?) ||
                (OS.linux? && Hardware::CPU.intel? && Hardware::CPU.is_64_bit?)
    odie "Brewdoc supports macOS ARM64 and Linux x64" unless supported
    odie "Brewdoc requires macOS 15 or newer" if OS.mac? && MacOS.version < :sequoia
    if OS.linux?
      glibc = Utils.safe_popen_read("/usr/bin/getconf", "GNU_LIBC_VERSION").strip
      parsed = /\Aglibc (\d+)\.(\d+)\z/.match(glibc)
      odie "Brewdoc requires system glibc 2.35 or newer" unless parsed && (parsed.captures.map(&:to_i) <=> [2, 35]) >= 0
    end
    unless File.executable?("brewdoc") && File.directory?("_internal") &&
           File.directory?("licenses") && File.file?("version.json")
      odie "Unexpected Brewdoc archive layout"
    end
    libexec.install Dir.children(".") - [".brew_home"]
    libexec.chmod 0755
    bin.install_symlink libexec/"brewdoc"
  end

  test do
    assert_match "self-check: ok", shell_output("#{{bin}}/brewdoc --self-check")
    (testpath/"probe.html").write("<html><body><p>Homebrew probe</p></body></html>")
    output = shell_output("#{{bin}}/brewdoc #{{testpath}}/probe.html")
    receipt, markdown = output.split("\n", 2)
    status = JSON.parse(receipt)
    assert_equal true, status.fetch("file_ok"), "The offline HTML probe must render"
    assert_equal "html", status.fetch("route"), "The local input must use the HTML route"
    assert_match "\nHomebrew probe\n", markdown
  end
end
'''


def check_document(text: str, version: str, policy: dict) -> None:
    """Refuse stale versions, unsupported assets and a nonexistent CLI flag."""
    specs = re.findall(r"brewdoc(?:\[render-js\])?(?:==|@)(\d+\.\d+\.\d+)", text)
    guides = re.findall(re.escape(REPOSITORY) + r"/blob/v(\d+\.\d+\.\d+)/(?:INSTALL|README|FORMATS)\.md", text)
    downloads = re.findall(r"/releases/download/v(\d+\.\d+\.\d+)/([^\s)\"'`]+)", text)
    assets = {f"brewdoc-{version}-{target}.{facts['archive']}" for target, facts in policy["targets"].items()}
    assets.update({f"brewdoc-{version}-py3-none-any.whl", f"brewdoc-{version}.tar.gz",
                   "SHA256SUMS", "binaries.json", "npm-manifest.json", "release-manifest.json"})
    assets.update(f"{name}-{version}.tgz" for name in (*policy["npm"]["packages"].values(), policy["npm"]["name"]))
    if ("brewdoc --version" in text or any(found != version for found in (*specs, *guides))
            or any(tag != version or name not in assets for tag, name in downloads)):
        raise ValueError("Maintained guide flag, version or asset differs")


def main(argv=None) -> int:
    """Render staged/public notes or check maintained docs; refuse before output writes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("render", "check", "homebrew"))
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
            for name in ("README.md", "INSTALL.md", "llms.txt"):
                check_document((args.checkout / name).read_text(encoding="utf-8"), version, policy)
            return 0
        if args.native_manifest is None or args.npm_manifest is None or args.output is None:
            parser.error("render requires --native-manifest, --npm-manifest and --output")
        facts = admit(args.checkout, args.native_manifest, args.npm_manifest, args.phase, args.availability)
        body = render_homebrew(facts) if args.mode == "homebrew" else render(facts)
        check_document(body, version, policy)
        args.output.write_text(body, encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Release guide refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
