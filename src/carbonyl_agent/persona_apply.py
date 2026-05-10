"""Persona → Chromium translator (W3C — `Refs: roctinam/carbonyl-agent#45`).

Bridges the schema-v2 ``Persona`` (defined in ``carbonyl-fingerprint``) to the
Chromium runtime that Carbonyl wraps. This module covers the *flag-driven*
half of the W3C surface: fields that map directly to Chromium command-line
options. The complementary content-script bundle (``navigator.platform``,
``screen.*``, WebGL ``getParameter`` overrides, canvas noise, timezone
fallback) requires Carbonyl-side support for ``--enable-content-script`` (or
a CDP hook) and lands in a follow-up PR once that surface stabilises.

Usage::

    from carbonyl_agent.persona_apply import Persona, persona_to_chromium_flags

    p = Persona.from_path("/path/to/persona.toml")           # validates by default
    flags = persona_to_chromium_flags(p)
    # → ["--user-agent=...", "--lang=en-US", "--accept-lang=en-US,en;q=0.9", ...]

Or pass a ``Persona`` instance directly to a browser::

    b = CarbonylBrowser(persona=p)
    b.open("https://example.com")

The string form ``CarbonylBrowser(persona="my_throwaway")`` continues to work
as a *profile name* keyed against ``ProfileManager`` — the typed ``Persona``
object is a strict superset that *also* drives Chromium flags.
"""
from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class PersonaValidationError(ValueError):
    """Raised when a persona TOML fails the ``carbonyl_fingerprint`` validator.

    The Rust validator's error list is preserved on ``errors`` so callers can
    route fixes (e.g., "regenerate canvas seed", "drop forbidden font")
    without parsing the message text.
    """

    def __init__(self, errors: list[str]) -> None:
        super().__init__(
            f"persona failed validation ({len(errors)} errors):\n  - "
            + "\n  - ".join(errors)
        )
        self.errors = list(errors)


class Persona:
    """Parsed, validated persona — wrapper around the TOML-decoded dict.

    Mirrors the schema in ``crates/carbonyl-fingerprint/src/schema.rs``
    (v2.0.0). Properties expose the fields ``persona_apply`` and the
    ``CarbonylBrowser`` integration consume; the underlying dict is
    available via :meth:`raw` for fields not yet exposed.

    Construction:
        - ``Persona.from_toml(toml_str)`` — parse, validate, wrap
        - ``Persona.from_path(path)`` — read file, parse, validate, wrap
        - ``Persona(data, validate=True)`` — wrap an already-parsed dict;
          set ``validate=False`` to bypass the Rust validator (test fixtures,
          partial personas, etc.)

    Validation goes through ``carbonyl_fingerprint.validate_toml`` so the
    Python and Rust call sites enforce the same rules — a divergence would
    immediately surface in cross-language tests.
    """

    __slots__ = ("_data",)

    def __init__(
        self,
        data: Mapping[str, Any],
        *,
        validate: bool = True,
    ) -> None:
        # Accept either {"persona": {...}} (TOML root) or {...} (already
        # unwrapped). Internally we always store the unwrapped form so
        # property accesses don't have to branch.
        if "persona" in data and isinstance(data["persona"], Mapping):
            inner = data["persona"]
        else:
            inner = data
        # Defensive copy — callers can keep their original dict.
        self._data: dict[str, Any] = _deep_dict(inner)

        if validate:
            self._run_validator()

    # ----- constructors -----

    @classmethod
    def from_toml(cls, toml_str: str, *, validate: bool = True) -> Persona:
        """Parse a persona TOML string. Raises :class:`PersonaValidationError`
        when ``validate`` is true and the persona fails the Rust validator."""
        data = tomllib.loads(toml_str)
        return cls(data, validate=validate)

    @classmethod
    def from_path(cls, path: str | Path, *, validate: bool = True) -> Persona:
        """Read a persona TOML file. See :meth:`from_toml`."""
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return cls(data, validate=validate)

    # ----- field accessors (typed) -----

    @property
    def id(self) -> str:
        return str(self._data["id"])

    @property
    def browser_family(self) -> str:
        """One of ``"chrome"``, ``"firefox"``, ``"safari"``."""
        return str(self._data["browser_family"])

    @property
    def browser_version(self) -> str:
        return str(self._data["browser_version"])

    @property
    def release_channel(self) -> str:
        return str(self._data["release_channel"])

    @property
    def user_agent_full(self) -> str:
        return str(self._data["user_agent"]["full"])

    @property
    def accept_language(self) -> str:
        """Accept-Language header value, e.g. ``"en-US,en;q=0.9"``."""
        return str(self._data["locale"]["accept_language"])

    @property
    def primary_language(self) -> str:
        """First locale in the Accept-Language list. ``--lang`` takes a
        single locale, not the full Accept-Language header."""
        return self.accept_language.split(",", 1)[0].strip()

    @property
    def timezone(self) -> str:
        """IANA timezone (e.g. ``"America/New_York"``). Applied via the
        ``TZ`` environment variable rather than a Chromium flag."""
        return str(self._data["locale"]["timezone"])

    @property
    def device_pixel_ratio(self) -> float:
        return float(self._data["device"]["device_pixel_ratio"])

    @property
    def screen_width(self) -> int:
        return int(self._data["device"]["screen_width"])

    @property
    def screen_height(self) -> int:
        return int(self._data["device"]["screen_height"])

    @property
    def viewport(self) -> tuple[int, int]:
        """``(width, height)`` shorthand for the ``viewport=`` argument
        on :class:`CarbonylBrowser`."""
        return (self.screen_width, self.screen_height)

    # ----- escape hatch -----

    def raw(self) -> Mapping[str, Any]:
        """Underlying parsed dict (read-only view). For fields the typed
        accessors don't yet expose — UA-CH brands, JA4, fonts, plugins,
        WebGL renderer, etc."""
        return self._data

    # ----- internals -----

    def _run_validator(self) -> None:
        # Lazy import: the PyO3 extension is an optional install for
        # consumers that don't need persona handling.
        try:
            import carbonyl_fingerprint as cf  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover — guarded path
            raise RuntimeError(
                "Persona validation requires the carbonyl_fingerprint "
                "extension. Build with: maturin develop --manifest-path "
                "crates/carbonyl-fingerprint/Cargo.toml --features python"
            ) from exc

        # Re-serialize through TOML so the validator gets the canonical
        # representation. Cheap (~tens of microseconds) and avoids
        # duplicating parse logic across the FFI boundary.
        try:
            import tomli_w

            toml_str = tomli_w.dumps({"persona": self._data})
        except ImportError:
            # tomli_w isn't a dependency yet; fall back to a hand-rolled
            # round-trip via the raw bytes path. This is guaranteed-valid
            # TOML when constructed from a TOML source — the only path
            # that produces an unserializable dict is direct
            # construction with bizarre values, which is a caller bug.
            toml_str = _dict_to_toml(self._data)

        ok, errors = cf.validate_toml(toml_str)
        if not ok:
            raise PersonaValidationError(errors)

    def __repr__(self) -> str:
        return (
            f"Persona(id={self.id!r}, family={self.browser_family!r}, "
            f"version={self.browser_version!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Persona):
            return NotImplemented
        return self._data == other._data

    def __hash__(self) -> int:
        # Personas are mutable in principle (raw() returns a live view) so
        # hashing is unsafe. Fall back to identity to satisfy collections
        # that require __hash__ when __eq__ is defined.
        return id(self)


def persona_to_chromium_flags(persona: Persona) -> list[str]:
    """Translate a :class:`Persona` to Chromium command-line flags.

    Covers the flag-driven half of W3C. Fields that need page-context
    overrides (navigator.*, screen.*, WebGL, canvas, plugin list) are NOT
    handled here — they require content-script injection, which lands in a
    follow-up PR alongside the Carbonyl-side ``--enable-content-script``
    surface (or a CDP hook, depending on which Carbonyl provides first).

    Returned flags are *additive* — call sites should append them after
    their base set (typically :data:`DEFAULT_HEADLESS_FLAGS`) so persona
    values override any persona-relevant defaults inherited from the base
    set (in practice: ``--user-agent``).

    Example::

        from carbonyl_agent.browser import BASE_CHROMIUM_FLAGS
        from carbonyl_agent.persona_apply import (
            Persona, persona_to_chromium_flags,
        )

        p = Persona.from_path("personas/ghost-01.toml")
        flags = BASE_CHROMIUM_FLAGS + persona_to_chromium_flags(p)
    """
    flags: list[str] = [
        f"--user-agent={persona.user_agent_full}",
        f"--lang={persona.primary_language}",
        f"--accept-lang={persona.accept_language}",
    ]
    # device_pixel_ratio = 1.0 is Chromium's own default; emitting the flag
    # would be a no-op and adds noise to the command line. Only emit when
    # the persona actually deviates.
    if persona.device_pixel_ratio != 1.0:
        flags.append(f"--force-device-scale-factor={persona.device_pixel_ratio}")
    return flags


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deep_dict(obj: Any) -> Any:
    """Recursive ``dict()``-cast for nested mappings.

    ``tomllib`` returns plain dicts/lists already, but third-party callers
    may pass ``Mapping`` / ``Sequence`` proxies. Normalize so the internal
    representation is JSON-shaped and serializable.
    """
    if isinstance(obj, Mapping):
        return {str(k): _deep_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_dict(v) for v in obj]
    return obj


def _dict_to_toml(data: Mapping[str, Any]) -> str:
    """Minimal dict → TOML serializer used only as a fallback when ``tomli_w``
    is unavailable. Handles the persona schema's shape: nested tables, lists,
    scalars (str/int/float/bool). Not a general-purpose TOML emitter.

    The validator path needs valid TOML; this serializer produces it for any
    input that came from ``tomllib.loads`` / ``tomllib.load``. Direct dict
    construction with exotic types (datetime, etc.) is unsupported — but the
    persona schema doesn't use those.
    """
    lines: list[str] = []
    _emit_table(lines, ["persona"], data)
    return "\n".join(lines) + "\n"


def _emit_table(lines: list[str], path: list[str], table: Mapping[str, Any]) -> None:
    # Two-pass: scalars first under [path], then sub-tables.
    scalars: list[tuple[str, Any]] = []
    subtables: list[tuple[str, Mapping[str, Any]]] = []
    for k, v in table.items():
        if isinstance(v, Mapping):
            subtables.append((k, v))
        else:
            scalars.append((k, v))

    if scalars or not subtables:
        lines.append("[" + ".".join(path) + "]")
        for k, v in scalars:
            lines.append(f"{k} = {_emit_value(v)}")
        lines.append("")

    for k, sub in subtables:
        _emit_table(lines, path + [k], sub)


def _emit_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        # TOML basic strings: escape backslash and double-quote, control chars.
        escaped = (
            v.replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    if isinstance(v, list):
        return "[" + ", ".join(_emit_value(item) for item in v) + "]"
    raise TypeError(f"cannot serialize {type(v).__name__} to TOML")
