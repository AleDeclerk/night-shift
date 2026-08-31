"""The two READMEs must not drift apart in silence."""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).parent.parent


def test_the_two_readmes_agree():
    """Two documents that say the same thing in two languages always drift.
    Nobody can derive Spanish prose from English prose, so a check that fails
    is the next best thing.
    """
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check-translations.py")],
        capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_check_catches_a_drift(tmp_path, monkeypatch):
    """A check that never fails proves nothing."""
    script = (ROOT / "scripts" / "check-translations.py").read_text()
    english = tmp_path / "README.md"
    spanish = tmp_path / "README.es.md"
    english.write_text("# t\n\n[es](README.es.md)\n\n## One\n\n    do this\n")
    spanish.write_text("# t\n\n[en](README.md)\n\n## Uno\n\n    do that\n")
    patched = tmp_path / "check.py"
    patched.write_text(script.replace(
        'ROOT = pathlib.Path(__file__).parent.parent',
        f'ROOT = pathlib.Path(r"{tmp_path}")'))
    done = subprocess.run([sys.executable, str(patched)], capture_output=True,
                          text=True)
    assert done.returncode == 1
    assert "commands differ" in done.stdout
