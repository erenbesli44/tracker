"""Minimal robots.txt matcher: RFC 9309 groups with ``*`` and ``$`` in rule paths.

The stdlib ``urllib.robotparser`` ignores both wildcards, so ``Disallow: /*.pdf$``
would not be honoured; this is the smallest replacement (no dependency).
"""

import re
from urllib.parse import urlsplit


class RobotsRules:
    """Allow/Disallow rules of one robots.txt that apply to one product token.

    Group selection: the group whose ``User-agent`` token (text before ``/``,
    case-insensitive) equals ours, else ``*``, else no rules. The longest matching
    rule path wins; ``Allow`` wins ties; an empty ``Disallow:`` allows everything.
    Rules are matched against ``path?query``.
    """

    def __init__(self, text: str, product_token: str) -> None:
        groups: dict[str, list[tuple[bool, str]]] = {}
        agents: list[str] = []  # User-agent lines of the group being read
        in_rules = False
        for raw in text.splitlines():
            key, sep, value = raw.split("#", 1)[0].partition(":")
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if in_rules:  # a new group starts after the previous group's rules
                    agents, in_rules = [], False
                agents.append(_token(value))
                groups.setdefault(agents[-1], [])
            elif key in ("allow", "disallow") and agents:
                in_rules = True
                for agent in agents:
                    groups[agent].append((key == "allow", value))
        self._rules = groups.get(_token(product_token), groups.get("*", []))

    @classmethod
    def disallow_all(cls) -> "RobotsRules":
        return cls("User-agent: *\nDisallow: /\n", "*")

    def can_fetch(self, url: str) -> bool:
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best_length, allowed = -1, True
        for allow, pattern in self._rules:
            if not pattern or not _matches(pattern, target):
                continue
            if len(pattern) > best_length or (len(pattern) == best_length and allow):
                best_length, allowed = len(pattern), allow
        return allowed


def _token(user_agent: str) -> str:
    return user_agent.split("/", 1)[0].strip().lower()


def _matches(pattern: str, target: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.match(regex + ("$" if anchored else ""), target) is not None
