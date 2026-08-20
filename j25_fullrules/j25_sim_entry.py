#!/usr/bin/env python3
"""Validated entry point for the resilient J25 runner.

Keeps Forge-version compatibility fixes separate from the main v3 runner so
we can audit them independently without rewriting the simulation core.

Forge 2.0.13 resolves ``NumAtt$ Double`` / ``NumDef$ Double`` correctly in the
game engine, but PumpAi still contains ``TODO add Double`` and asks
AbilityUtils.calculateAmount() to interpret the literal ``Double``. That emits
missing-SVar warnings and evaluates the pump as zero for AI decision-making.
J25 hits this on Reckless Amplimancer and Unleash Fury.

Before simulation we rewrite only those two pinned Forge card scripts to
semantically equivalent arithmetic forms already supported by Forge. The
actual Magic effects and Oracle text are unchanged. Exact old-script matching
makes this fail loudly if the pinned runtime changes unexpectedly.
"""
from pathlib import Path
import re
import sys

import j25_sim_v3 as impl

# Forge 2.0.13 emits e.g. "fallback to Card (Reckless Amplimancer)".
impl.CARD_RE = re.compile(r"Card \(([^)]+)\)")

_PATCHES = {
    "r/reckless_amplimancer.txt": (
        "A:AB$ Pump | Cost$ 4 G | NumAtt$ Double | NumDef$ Double | StackDescription$ SpellDescription | SpellDescription$ Double CARDNAME's power and toughness until end of turn.\n",
        "A:AB$ Pump | Cost$ 4 G | NumAtt$ +X | NumDef$ +Y | StackDescription$ SpellDescription | SpellDescription$ Double CARDNAME's power and toughness until end of turn.\nSVar:X:Count$CardPower\nSVar:Y:Count$CardToughness\n",
    ),
    "u/unleash_fury.txt": (
        "A:SP$ Pump | ValidTgts$ Creature | NumAtt$ Double | StackDescription$ REP target creature_{c:Targeted} | SpellDescription$ Double the power of target creature until end of turn.\n",
        "A:SP$ Pump | ValidTgts$ Creature | NumAtt$ +X | StackDescription$ REP target creature_{c:Targeted} | SpellDescription$ Double the power of target creature until end of turn.\nSVar:X:Targeted$CardPower\n",
    ),
}


def _arg_value(flag: str):
    try:
        return sys.argv[sys.argv.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def patch_forge_double_pumps() -> None:
    jar = _arg_value("--forge-jar")
    if not jar:
        return
    cards = Path(jar).resolve().parent / "res" / "cardsfolder"
    if not cards.is_dir():
        raise RuntimeError(f"Forge cardsfolder not found for compatibility patch: {cards}")
    for rel, (old, new) in _PATCHES.items():
        path = cards / rel
        text = path.read_text(encoding="utf-8")
        if new in text:
            print(f"FORGE_AI_PATCH already_applied {rel}")
            continue
        if old not in text:
            raise RuntimeError(f"Forge compatibility patch source changed unexpectedly: {rel}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        print(f"FORGE_AI_PATCH applied {rel}")


if __name__ == "__main__":
    patch_forge_double_pumps()
    impl.main()
