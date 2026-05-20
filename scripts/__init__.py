"""Repo-local helper scripts (mint JWTs, seed fixtures, smoke prod).

Made a package so eval.py can `from scripts import _eval_setup` without
sys.path mangling. No public exports — direct script invocation is the
primary use case.
"""
