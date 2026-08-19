#!/usr/bin/env python3
"""Validated entry point for the resilient J25 runner.

Keeps tiny log-format compatibility patches separate from the main v3 runner so
we can audit them independently without rewriting the simulation core.
"""
import re
import j25_sim_v3 as impl

# Forge 2.0.13 emits e.g. "fallback to Card (Reckless Amplimancer)".
impl.CARD_RE = re.compile(r"Card \(([^)]+)\)")

if __name__ == "__main__":
    impl.main()
