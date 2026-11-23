"""Permissions: which role may call which tool and which rows it may see.

The role table lives as data in roles.json. Permission is applied in two layers:
1. Tool: a tool that is not in the role's list is never run.
2. Data: the tool runs on a connection that holds no rows other than those in the role's scope (scope.py). Even
   if there is a bug in a tool, an out-of-scope row cannot enter the result.

All roles are shown to the model with the same tool list: a separate list per role would make the model re-read
the tool definitions every time the role changes. In Phase 0 the 15 draft tools took 2,078 tokens and the 9b
model read them in ~77 seconds; the 13-tool list measured at the time is 2,187 tokens (measurement:
reports/faz3_araclar.json).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROLES_FILE = Path(__file__).with_name("roles.json")
SCOPES = ("hepsi", "yurtici", "ihracat")


@dataclass(frozen=True)
class Role:
    role_id: str
    name: str
    scope: str
    tools: frozenset[str]


@dataclass(frozen=True)
class PermissionTable:
    roller: dict[str, Role]
    users: dict[str, str]  # user -> role id

    def is_allowed(self, role: str, tool: str) -> bool:
        r = self.roller.get(role)
        return r is not None and tool in r.tools


def read_permissions(path: Path = ROLES_FILE) -> PermissionTable:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    roles = {}
    for role_id, r in data["roller"].items():
        if r["kapsam"] not in SCOPES:
            raise ValueError(f"bilinmeyen kapsam: {r['kapsam']}")
        roles[role_id] = Role(role_id, r["ad"], r["kapsam"], frozenset(r["araclar"]))
    for user, role in data["kullanicilar"].items():
        if role not in roles:
            raise ValueError(f"{user} için bilinmeyen rol: {role}")
    return PermissionTable(roles, dict(data["kullanicilar"]))
