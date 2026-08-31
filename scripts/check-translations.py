#!/usr/bin/env python3
"""Fail when the two READMEs drift apart.

Two documents that say the same thing in two languages always drift, and the
drift never announces itself. Nobody can derive Spanish prose from English
prose, so this script does the next best thing: it compares the parts that must
match anyway, and it fails when they do not.

It checks the shape and the facts, never the wording:
  - the same number of headings, at the same levels, in the same order
  - the same indented command blocks, character for character
  - the same numbers in the measurement table
  - the same links to files of this repository

Run it: python3 scripts/check-translations.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent.parent
PAIR = (ROOT / "README.md", ROOT / "README.es.md")

# A command block is indented with four spaces. Those are instructions, so the
# two languages must give exactly the same ones.
COMMAND = re.compile(r"^    (\S.*)$", re.M)
HEADING = re.compile(r"^(#+) ", re.M)
NUMBER = re.compile(r"\b\d+\.\d+\b")
REPO_LINK = re.compile(r"\]\((?!http)([^)]+)\)")


def read(path: pathlib.Path) -> str:
    if not path.exists():
        sys.exit(f"missing file: {path}")
    return path.read_text()


def compare(name: str, left: list, right: list, problems: list) -> None:
    if left == right:
        return
    only_en = [x for x in left if x not in right]
    only_es = [x for x in right if x not in left]
    problems.append(
        f"{name} differ:\n"
        f"    only in README.md:    {only_en or '(none)'}\n"
        f"    only in README.es.md: {only_es or '(none)'}")


def main() -> int:
    english, spanish = (read(p) for p in PAIR)
    problems: list = []

    compare("heading levels", HEADING.findall(english), HEADING.findall(spanish),
            problems)
    compare("commands", COMMAND.findall(english), COMMAND.findall(spanish),
            problems)
    compare("numbers", sorted(NUMBER.findall(english)),
            sorted(NUMBER.findall(spanish)), problems)
    # The link to the other language is the one link that must differ.
    def repo_links(text: str) -> list:
        return sorted(x for x in REPO_LINK.findall(text)
                      if x not in ("README.md", "README.es.md"))

    compare("links inside the repository", repo_links(english),
            repo_links(spanish), problems)

    if "README.es.md" not in english:
        problems.append("README.md does not link to README.es.md")
    if "README.md" not in spanish:
        problems.append("README.es.md does not link to README.md")

    if problems:
        print("The two READMEs drifted apart.\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("The two READMEs agree on shape, commands, numbers and links.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
