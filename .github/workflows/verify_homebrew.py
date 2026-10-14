"""Verify published Homebrew upgrades against the installed native bundle bytes."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile

SPEC = importlib.util.spec_from_file_location("release_assets", Path(__file__).with_name("release_assets.py"))
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)
native = assets.helper("build_standalone")
guide = assets.helper("release_guide")
TAP = "kochetkov-ma/brew"
TAP_REPOSITORY = "kochetkov-ma/homebrew-brew"
FORMULA = TAP + "/brewdoc"


def verify_inventory(libexec: Path, manifest: dict, output: Path) -> dict:
    """Retain the actual installed tree and refuse any byte, mode or internal-link drift."""
    actual = native.inventory(libexec, logical_root="brewdoc")
    assets.write_json(output, actual)
    if actual != manifest["inventory"]:
        raise ValueError("Installed Homebrew inventory differs")
    return actual


def public_native(tag: str, output: Path) -> tuple[dict, dict]:
    """Download public controls and bind every native identity to immutable release metadata."""
    release = assets.api("releases/tags/" + tag)
    if (release["tag_name"], release["draft"], release["prerelease"]) != (tag, False, False):
        raise ValueError("Homebrew input release is not public and stable")
    rows = release["assets"]
    by_name = {row["name"]: row for row in rows}
    if len(rows) != len(by_name) or len(rows) != 12:
        raise ValueError("Homebrew public release asset set differs")
    output.mkdir()
    for name in ("release-manifest.json", "binaries.json", "SHA256SUMS"):
        row = by_name[name]
        digest = row.get("digest", "").removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Homebrew public control digest is unavailable")
        assets.download(assets.public_url(tag, name), output / name,
                        {"bytes": row["size"], "sha256": digest})
    seal = assets.read_json(output / "release-manifest.json")
    binaries = assets.read_json(output / "binaries.json")
    if (seal["schema"], seal["tag"], seal["version"], binaries["schema"], binaries["version"],
            binaries["source_sha"]) != (1, tag, tag.removeprefix("v"), 1, seal["version"], seal["source_sha"]):
        raise ValueError("Homebrew public source/version identity differs")
    assets.tag_preflight(seal)
    if set(by_name) != set(seal["assets"]) | {"release-manifest.json", "SHA256SUMS"}:
        raise ValueError("Homebrew public seal inventory differs")
    for name, identity in seal["assets"].items():
        row = by_name[name]
        if (row["size"], row.get("digest")) != (identity["bytes"], "sha256:" + identity["sha256"]):
            raise ValueError("Homebrew public asset identity differs")
    if assets.identity(output / "binaries.json") != seal["assets"]["binaries.json"]:
        raise ValueError("Homebrew native control bytes differ")
    if (output / "SHA256SUMS").read_text(encoding="ascii") != assets.checksums(seal["assets"]):
        raise ValueError("Homebrew public checksums differ")
    if set(binaries["targets"]) != set(assets.TARGETS):
        raise ValueError("Homebrew public native targets differ")
    for target, record in binaries["targets"].items():
        native.validate_manifest(record)
        if (record["version"], record["source_sha"], record["target"]) != (
                seal["version"], seal["source_sha"], target):
            raise ValueError("Homebrew native source/target identity differs")
        archive = record["archive"]
        if {key: archive[key] for key in ("bytes", "sha256")} != seal["assets"][archive["filename"]]:
            raise ValueError("Homebrew native archive seal differs")
        wheel = seal["assets"][f"brewdoc-{seal['version']}-py3-none-any.whl"]
        if record["wheel_sha256"] != wheel["sha256"]:
            raise ValueError("Homebrew native wheel identity differs")
    assets.write_json(output / "public-release.json", release)
    return seal, binaries


def published_formula(sha: str, binaries: dict) -> dict:
    """Require the actual published tap formula to equal admitted immutable native facts."""
    command = ["gh", "api", f"repos/{TAP_REPOSITORY}/contents/Formula/brewdoc.rb?ref={sha}"]
    record = json.loads(assets.command(command))
    if ((record["type"], record["path"], record["encoding"]) != ("file", "Formula/brewdoc.rb", "base64")
            or type(record["size"]) is not int or not 0 < record["size"] <= 65536):
        raise ValueError("Published Homebrew formula path/type differs")
    payload = base64.b64decode(record["content"].replace("\n", ""), validate=True)
    if len(payload) != record["size"] or len(payload) > 65536:
        raise ValueError("Published Homebrew formula size differs")
    facts = {"phase": "public", "version": binaries["version"], "source_sha": binaries["source_sha"],
             "targets": binaries["targets"]}
    if payload != guide.render_homebrew(facts).encode("utf-8"):
        raise ValueError("Published Homebrew formula differs from admitted native facts")
    return {"tap_sha": sha, "version": binaries["version"],
            "source_sha": binaries["source_sha"]}


def installed_probe(binary: Path, manifest: dict, work: Path) -> dict:
    """Exercise existing sealed probes and process boundaries through the installed executable."""
    work.mkdir()
    environment = native.consumer_environment(work)
    check = native.command([str(binary), "--self-check"], cwd=work, env=environment, timeout=30)
    if (check.stdout, check.stderr) != (b"self-check: ok (38 checks)\n", b""):
        raise ValueError("Installed Homebrew self-check differs")
    probes = []
    if native.verification_scope(manifest) != "smoke":
        raise ValueError("Homebrew consumer requires the admitted native smoke profile")
    for row in manifest["probes"]:
        source = work / row["path"]
        source.write_bytes(base64.b64decode(row["bytes_base64"], validate=True))
        if native.digest(source) != row["source_sha256"]:
            raise ValueError("Installed Homebrew probe source differs")
        arguments = [str(binary), str(source)]
        for sheet in row.get("sheets", []):
            arguments += ["--sheet", sheet]
        artifact = None
        if "artifact" in row:
            artifact = work / (row["probe"] + ".payload")
            arguments += ["--artifact", row["artifact"]["key"] + "=" + str(artifact)]
        result = native.command(arguments, cwd=work, env=environment, timeout=60)
        native.check_output(result, row, artifact_output=artifact)
        destination = work / (row["probe"] + ".md")
        result = native.command([*arguments, "--out", str(destination)], cwd=work, env=environment, timeout=60)
        native.check_output(result, row, destination, artifact)
        if artifact is not None and assets.identity(artifact) != {
                key: row["artifact"][key] for key in ("bytes", "sha256")}:
            raise ValueError("Installed Homebrew workbook artifact differs")
        probes.append(row["probe"])
    refused = native.zip_xml_refusal_probe([str(binary)], work, environment)
    children = native.child_checks(binary, work, environment, True)
    return {"self_check": 38, "probes": probes, "zip_xml_refusal": refused, "children": children}


def verify(args: argparse.Namespace) -> None:
    """Install a real published bootstrap, upgrade, and retain only exact installed acceptance."""
    version = guide.project_version(args.checkout)
    if (not re.fullmatch(r"[0-9a-f]{40}", args.source_sha)
            or not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (args.bootstrap_tap_sha, args.tap_sha))
            or args.bootstrap_tap_sha == args.tap_sha or args.tag != "v" + version or version == "0.4.1"):
        raise ValueError("Homebrew dispatch identities differ")
    if assets.command(["git", "-C", str(args.checkout), "rev-parse", "HEAD"]).decode().strip() != args.source_sha:
        raise ValueError("Homebrew checkout source differs")
    if (not re.fullmatch(r"[0-9a-f]{40}", args.tool_source_sha)
            or assets.command(["git", "-C", str(assets.CHECKOUT), "rev-parse", "HEAD"]).decode().strip() != args.tool_source_sha):
        raise ValueError("Homebrew tool source differs")
    native.facts(args.checkout, args.target)
    if not args.brew.is_absolute() or not args.brew.is_file():
        raise ValueError("Homebrew executable must be an existing absolute path")
    args.output.mkdir(parents=True, exist_ok=False)
    baseline, old_binaries = public_native("v0.4.1", args.output / "bootstrap")
    seal, binaries = public_native(args.tag, args.output / "current")
    if seal["source_sha"] != args.source_sha or baseline["source_sha"] == args.source_sha:
        raise ValueError("Homebrew current/bootstrap source identity differs")
    formulas = [published_formula(args.bootstrap_tap_sha, old_binaries), published_formula(args.tap_sha, binaries)]
    head = json.loads(assets.command(["gh", "api", f"repos/{TAP_REPOSITORY}/git/ref/heads/main"]))
    if head["object"]["sha"] != args.tap_sha:
        raise ValueError("Published Homebrew tap head differs")
    environment = {key: value for key, value in os.environ.items() if key not in {
        "GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL",
        "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}}
    environment.update({"HOMEBREW_NO_AUTO_UPDATE": "1",
                        "HOMEBREW_NO_INSTALL_CLEANUP": "1", "HOMEBREW_NO_ANALYTICS": "1"})
    commands = []

    def brew(*arguments: str) -> bytes:
        """Save bounded actual command output, including the first failed lifecycle step."""
        result = subprocess.run([str(args.brew), *arguments], env=environment, capture_output=True, timeout=600)
        number = len(commands)
        (args.output / f"command-{number}.stdout").write_bytes(result.stdout)
        (args.output / f"command-{number}.stderr").write_bytes(result.stderr)
        commands.append({"argv": [str(args.brew), *arguments], "exit": result.returncode})
        assets.write_json(args.output / "commands.json", {"commands": commands})
        if result.returncode:
            raise ValueError("Homebrew lifecycle command failed: " + arguments[0])
        return result.stdout

    brew_version = brew("--version").decode().strip()
    brew_repository = Path(brew("--repository").decode().strip())
    brew_sha = assets.command(["git", "-C", str(brew_repository), "rev-parse", "HEAD"]).decode().strip()
    if "brewdoc" in brew("list", "--formula").decode().splitlines() or TAP in brew("tap").decode().splitlines():
        raise ValueError("Homebrew consumer requires a fresh formula and tap")
    brew("tap", TAP)
    tap = Path(brew("--repository", TAP).decode().strip())
    assets.command(["git", "-C", str(tap), "merge-base", "--is-ancestor", args.bootstrap_tap_sha, args.tap_sha])
    reports = {}
    with tempfile.TemporaryDirectory(prefix="brewdoc Homebrew consumer ") as temporary:
        work = Path(temporary)
        sentinel = work / "user-input.txt"
        output_sentinel = work / "user-output.md"
        sentinel.write_bytes(b"preserve user input\n")
        output_sentinel.write_bytes(b"preserve user output\n")
        for phase, sha, aggregate, operation in (
                ("bootstrap", args.bootstrap_tap_sha, old_binaries, "install"),
                ("current", args.tap_sha, binaries, "upgrade")):
            assets.command(["git", "-C", str(tap), "checkout", "--detach", sha])
            if (tap / "Formula/brewdoc.rb").read_text(encoding="utf-8") != guide.render_homebrew({
                    "phase": "public", "version": aggregate["version"], "source_sha": aggregate["source_sha"],
                    "targets": aggregate["targets"]}):
                raise ValueError("Cloned published Homebrew formula differs")
            brew(operation, "--build-from-source", FORMULA)
            keg = Path(brew("--prefix", FORMULA).decode().strip()).resolve(strict=True)
            manifest = aggregate["targets"][args.target]
            libexec = keg / "libexec"
            verify_inventory(libexec, manifest, args.output / (phase + "-inventory.json"))
            expected = {key: manifest[key] for key in ("version", "source_sha", "python", "target")}
            if assets.read_json(libexec / "version.json") != expected:
                raise ValueError("Installed Homebrew version identity differs")
            linked = Path(brew("--prefix").decode().strip()) / "bin/brewdoc"
            binary = libexec / "brewdoc"
            if not linked.is_symlink() or linked.resolve(strict=True) != binary.resolve(strict=True):
                raise ValueError("Homebrew linked executable differs")
            brew("test", FORMULA)
            reports[phase] = {"identity": expected, "keg": str(keg),
                              "consumer": installed_probe(linked, manifest, work / phase)}
            assets.write_json(args.output / (phase + "-consumer.json"), reports[phase])
        brew("unlink", FORMULA)
        if linked.exists() or linked.is_symlink():
            raise ValueError("Homebrew unlink retained its executable")
        brew("link", FORMULA)
        if not linked.is_symlink() or linked.resolve(strict=True) != binary.resolve(strict=True):
            raise ValueError("Homebrew relink selected a different executable")
        verify_inventory(libexec, manifest, args.output / "relinked-inventory.json")
        brew("uninstall", "--force", FORMULA)
        if linked.exists() or linked.is_symlink() or any(Path(row["keg"]).exists() for row in reports.values()):
            raise ValueError("Homebrew uninstall retained an accepted keg or link")
        if (sentinel.read_bytes(), output_sentinel.read_bytes()) != (b"preserve user input\n", b"preserve user output\n"):
            raise ValueError("Homebrew lifecycle changed unrelated user files")
        brew("untap", TAP)
    assets.write_json(args.output / "verification.json", {
        "schema": 1, "verdict": "PASS", "version": version, "source_sha": args.source_sha,
        "tool_source_sha": args.tool_source_sha,
        "tag": args.tag, "target": args.target, "host": platform.platform(), "brew": brew_version,
        "brew_sha": brew_sha,
        "runner_image": {key: os.environ.get(key) for key in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH")},
        "formulas": formulas, "installed": reports, "unlink_relink_uninstall": "PASS",
        "unrelated_user_files_preserved": True})


def main(argv: list[str] | None = None) -> int:
    """Expose one post-publication lifecycle verification with exact release and tap identities."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("checkout", "brew", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("tag", "source-sha", "tool-source-sha", "bootstrap-tap-sha", "tap-sha"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--target", choices=("linux-x64", "macos-arm64"), required=True)
    args = parser.parse_args(argv)
    try:
        verify(args)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Homebrew verification refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
