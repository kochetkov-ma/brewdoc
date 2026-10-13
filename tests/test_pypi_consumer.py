"""Exercise exact-version propagation without real waits or public registry access."""

import importlib.util
from pathlib import Path
import subprocess

import pytest

VERSION = "0.3.3"
ABSENT = ("Using Python 3.12.14 environment at: /consumer\n"
          "error: No solution found when resolving dependencies\n"
          "  cause: Because there is no version of brewdoc==0.3.3 and you require brewdoc==0.3.3, "
          "we can conclude that your requirements are unsatisfiable.\n").encode()


@pytest.fixture
def consumer():
    """Load the release helper independently of product imports."""
    path = Path(__file__).parents[1] / ".github/workflows/pypi_consumer.py"
    spec = importlib.util.spec_from_file_location("pypi_consumer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def timing():
    """Advance the injected monotonic clock only when the helper waits."""
    times = [0.0]
    sleeps = []

    def sleep(seconds):
        """Record and advance virtual time without sleeping."""
        sleeps.append(seconds)
        times[0] += seconds

    return times, sleeps, sleep


@pytest.mark.parametrize("route,selection", [("wheel", "--only-binary"), ("sdist", "--no-binary")])
def test_exact_pypi_version_absent_twice_then_visible_passes(consumer, timing, route, selection):
    # GIVEN two cached absence answers followed by a visible exact requested version
    times, sleeps, sleep = timing
    responses = iter([subprocess.CompletedProcess([], 1, b"", ABSENT)] * 2
                     + [subprocess.CompletedProcess([], 0, b"installed", b"")])
    calls = []

    def run(command, **options):
        """Record each bounded refreshed install attempt."""
        calls.append((command, options))
        return next(responses)

    assert calls == [], "no install starts before the consumer request"
    # WHEN the normal wheel or forced source distribution waits for visibility
    result = consumer.install(VERSION, "/consumer/bin/python", route,
                              run=run, clock=lambda: times[0], sleep=sleep)
    # THEN only exact-version absence is retried with a refreshed package index
    expected = ["uv", "pip", "install", "--python", "/consumer/bin/python", "--index-url",
                "https://pypi.org/simple", selection, "brewdoc", "--refresh-package", "brewdoc", "brewdoc==0.3.3"]
    assert (result.returncode, result.stdout, result.stderr) == (0, b"installed", b""), "the exact visible version must pass"
    assert calls == [(expected, {"capture_output": True, "timeout": budget}) for budget in [600, 585, 570]], (
        "every attempt must refresh only brewdoc and fit the remaining ten-minute deadline")
    assert sleeps == [15, 15], "only absent reads wait, using the virtual sleeper"


@pytest.mark.parametrize("route", ["wheel", "sdist"])
def test_exact_pypi_version_still_absent_at_bound_fails(consumer, timing, route):
    # GIVEN an exact-version absence throughout the ten-minute visibility budget
    times, sleeps, sleep = timing
    calls = []

    def run(command, **options):
        """Always report absence and retain each process deadline."""
        calls.append(options["timeout"])
        return subprocess.CompletedProcess(command, 1, b"", ABSENT)

    assert times == [0.0], "the deadline starts at virtual time zero"
    # WHEN the registry never serves the requested version
    result = consumer.install(VERSION, "/consumer/bin/python", route,
                              run=run, clock=lambda: times[0], sleep=sleep)
    # THEN it stops without an extra install after the deadline and preserves uv's failure
    assert (result.returncode, result.stderr) == (1, ABSENT), "expired absence must retain the original uv error"
    assert (times, sleeps, calls) == ([600.0], [15] * 40, list(range(600, 0, -15))), (
        "forty virtual intervals exhaust the bound with no real sleep")


@pytest.mark.parametrize("code,stderr", [
    (2, b"error: Failed to fetch the package index\n"),
    (1, ABSENT.replace(b"brewdoc==0.3.3", b"brewdoc==0.3.2")),
    (1, ABSENT.replace(b"brewdoc==0.3.3", b"pdfplumber==0.3.3")),
    (1, ABSENT + b"error: Invalid metadata\n"),
    (1, b"error: No solution found when resolving dependencies\n  cause: dependencies conflict\n"),
    (2, ABSENT),
])
def test_unrelated_install_failures_are_not_retried(consumer, timing, code, stderr):
    # GIVEN an install failure other than the pinned exact-version absence diagnostic
    times, sleeps, sleep = timing
    calls = []
    failure = subprocess.CompletedProcess([], code, b"original stdout", stderr)

    def run(command, **options):
        """Return the original failure without a registry read."""
        calls.append(command)
        return failure

    assert calls == [], "the first failure has not occurred yet"
    # WHEN publication visibility is checked
    result = consumer.install(VERSION, "/consumer/bin/python", "wheel",
                              run=run, clock=lambda: times[0], sleep=sleep)
    # THEN other failures preserve their exit and streams on the first attempt
    assert result is failure, "the original unrelated uv failure must be returned unchanged"
    assert (len(calls), times, sleeps) == (1, [0.0], []), "unrelated failures must never wait or retry"


def test_install_process_cannot_outlive_remaining_visibility_budget(consumer, timing):
    # GIVEN one slow missing-version read that leaves seven seconds in the budget
    times, sleeps, sleep = timing
    calls = []

    def run(command, **options):
        """Consume nearly the entire visibility budget in one read."""
        calls.append(options["timeout"])
        times[0] += 593
        return subprocess.CompletedProcess(command, 1, b"", ABSENT)

    assert consumer.WAIT_SECONDS == 600, "the documented budget must be ten minutes"
    # WHEN the first attempt consumes almost all available time
    result = consumer.install(VERSION, "/consumer/bin/python", "wheel",
                              run=run, clock=lambda: times[0], sleep=sleep)
    # THEN the final wait is capped and no further process starts
    assert (result.returncode, calls, times, sleeps) == (1, [600], [600.0], [7]), (
        "subprocess work and polling must share the same monotonic deadline")


def test_install_subprocess_timeout_is_an_immediate_failure(consumer, timing):
    # GIVEN a command that exceeds its assigned process timeout
    times, sleeps, sleep = timing
    calls = []

    def run(command, **options):
        """Raise the actual subprocess timeout without sleeping."""
        calls.append(options["timeout"])
        raise subprocess.TimeoutExpired(command, options["timeout"])

    assert calls == [], "no timed-out command has run yet"
    # WHEN the install exceeds its visibility deadline
    # THEN it fails immediately instead of treating process failure as index absence
    with pytest.raises(subprocess.TimeoutExpired):
        consumer.install(VERSION, "/consumer/bin/python", "wheel",
                         run=run, clock=lambda: times[0], sleep=sleep)
    assert (calls, sleeps) == ([600], []), "the subprocess receives the whole remaining bounded budget"


@pytest.mark.parametrize("elapsed", [600, 601])
def test_visible_install_finishing_after_deadline_is_not_admitted(consumer, timing, elapsed):
    # GIVEN an install that returns success at or after the ten-minute deadline
    times, sleeps, sleep = timing

    def run(command, **options):
        """Model subprocess startup or scheduling consuming the whole budget."""
        times[0] += elapsed
        return subprocess.CompletedProcess(command, 0, b"late success", b"")

    assert times == [0.0], "no install time has elapsed before invocation"
    # WHEN a late subprocess result reaches admission
    # THEN a visible version cannot bypass the monotonic deadline
    with pytest.raises(subprocess.TimeoutExpired):
        consumer.install(VERSION, "/consumer/bin/python", "wheel",
                         run=run, clock=lambda: times[0], sleep=sleep)
    assert (times, sleeps) == ([float(elapsed)], []), "a late success must fail without further reads or waits"


def test_consumer_cli_preserves_original_uv_exit_and_streams(consumer, monkeypatch, capsys):
    # GIVEN an unrelated uv failure with its original streams
    failure = subprocess.CompletedProcess([], 2, b"original stdout\n", b"original failure\n")
    monkeypatch.setattr(consumer, "install", lambda *args: failure)
    assert failure.returncode == 2, "the failure must differ from retryable exact-version absence"
    # WHEN the helper exits after an unrelated installation failure
    code = consumer.main(["--version", VERSION, "--python", "/consumer/bin/python", "--route", "wheel"])
    # THEN the workflow receives the unchanged uv failure and output
    output = capsys.readouterr()
    assert (code, output.out, output.err) == (2, "original stdout\n", "original failure\n"), (
        "the helper must preserve unrelated uv error visibility")


def test_consumer_cli_reports_bounded_process_timeout(consumer, monkeypatch, capsys):
    # GIVEN an installation command that exceeds the allotted deadline
    def expired(*args):
        """Raise a subprocess timeout without starting a process."""
        raise subprocess.TimeoutExpired(["uv"], 600)

    monkeypatch.setattr(consumer, "install", expired)
    assert consumer.WAIT_SECONDS == 600, "the CLI diagnostic must reflect the documented bound"
    # WHEN the consumer install exhausts its deadline
    code = consumer.main(["--version", VERSION, "--python", "/consumer/bin/python", "--route", "sdist"])
    # THEN the workflow receives a clear nonzero failure without command or environment data
    output = capsys.readouterr()
    assert (code, output.out, output.err) == (1, "", "Exact PyPI consumer install exceeded its 600-second deadline\n"), (
        "timeout diagnostics must identify the exhausted visibility budget")
