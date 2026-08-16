"""Typed command intent parser with aliases and unambiguous prefixes."""

from __future__ import annotations

from dataclasses import dataclass
import difflib

from beta_earth.domain.actions import RecoveryClass


class CommandParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    aliases: tuple[str, ...] = ()
    recovery: RecoveryClass = RecoveryClass.SOFT
    minimum_prefix: int = 2
    summary: str = ""

    @property
    def hard(self) -> bool:
        return self.recovery is RecoveryClass.HARD


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    name: str
    args: tuple[str, ...]
    raw: str
    recovery: RecoveryClass

    @property
    def hard(self) -> bool:
        return self.recovery is RecoveryClass.HARD


DIRECTIONS = {
    "n": "north",
    "north": "north",
    "ne": "northeast",
    "northeast": "northeast",
    "e": "east",
    "east": "east",
    "se": "southeast",
    "southeast": "southeast",
    "s": "south",
    "south": "south",
    "sw": "southwest",
    "southwest": "southwest",
    "w": "west",
    "west": "west",
    "nw": "northwest",
    "northwest": "northwest",
    "u": "up",
    "up": "up",
    "d": "down",
    "down": "down",
    "out": "out",
}


DEFAULT_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("look", ("l",), summary="look [thing] - view the room or an object"),
    CommandSpec(
        "examine", ("exa", "inspect", "x"), summary="examine THING - inspect closely"
    ),
    CommandSpec("glance", ("gl",), summary="glance - compact room view"),
    CommandSpec("exits", ("directions", "dirs"), summary="exits - list obvious paths"),
    CommandSpec(
        "route",
        ("pathfind", "wayfind"),
        summary=(
            "route [KNOWN PLACE|OBJECTIVE] - show spatial memory or a shortest path"
        ),
    ),
    CommandSpec(
        "briefing",
        ("recap", "resume"),
        summary=(
            "briefing - review your objective, checkpoint, route, and Sol's status"
        ),
    ),
    CommandSpec(
        "next",
        ("hint", "step"),
        summary=(
            "next [FULL] - show one exact, reward-neutral step toward the active objective"
        ),
    ),
    CommandSpec(
        "playtest",
        ("stopwatch", "timing"),
        summary=(
            "playtest [PLAN|CHECKLIST|START|STATUS|PAUSE|RESUME|ISSUE|TOOL|NOTE|"
            "SURVEY|COMPLETE|RECEIPT] - record bounded local cohort evidence"
        ),
    ),
    CommandSpec(
        "journal",
        ("discoveries", "fieldlog"),
        summary="journal [LOCATIONS|CLUES|COURSES|VICTORIES|PROGRESS] - review discoveries",
    ),
    CommandSpec("go", ("walk", "move"), RecoveryClass.HARD, summary="go DIRECTION - move"),
    CommandSpec(
        "withdraw",
        ("retreat", "disengage"),
        RecoveryClass.HARD,
        summary="withdraw [STATUS [DIRECTION]|DIRECTION] - inspect exact odds or contest an exit",
    ),
    CommandSpec("get", ("take",), RecoveryClass.HARD, summary="get ITEM - pick something up"),
    CommandSpec("drop", (), RecoveryClass.HARD, summary="drop ITEM - place it here"),
    CommandSpec("inventory", ("i", "inv"), summary="inventory - list carried items"),
    CommandSpec("equipment", ("gear",), summary="equipment - list worn and held gear"),
    CommandSpec(
        "compare",
        ("comparegear",),
        summary="compare ITEM - compare carried equipment with the active slot",
    ),
    CommandSpec("equip", ("wear", "wield"), RecoveryClass.HARD, summary="equip ITEM - ready gear"),
    CommandSpec("unequip", ("remove", "unwear"), RecoveryClass.HARD, summary="unequip ITEM - stow gear"),
    CommandSpec(
        "repair",
        ("mend",),
        RecoveryClass.HARD,
        summary="repair ITEM - consume matching material at a repair bench",
    ),
    CommandSpec(
        "modify",
        ("mod", "upgrade"),
        RecoveryClass.HARD,
        summary="modify ITEM - consume a field mod kit at a repair bench",
    ),
    CommandSpec(
        "technique",
        ("signature", "classaction"),
        RecoveryClass.HARD,
        summary="technique [TARGET|DIRECTION] - use your class signature action",
    ),
    CommandSpec(
        "ability",
        ("specialization", "branch"),
        RecoveryClass.HARD,
        summary="ability [LEARN NAME|USE TARGET] - choose or use a class branch",
    ),
    CommandSpec(
        "market",
        ("trade", "vendor"),
        RecoveryClass.HARD,
        summary="market [BUY ITEM|SELL ITEM] - trade at the local exchange",
    ),
    CommandSpec(
        "craft",
        (),
        RecoveryClass.HARD,
        summary="craft [RECIPE] - create an item at the required facility",
    ),
    CommandSpec(
        "salvage",
        ("dismantle",),
        RecoveryClass.HARD,
        summary="salvage ITEM - dismantle unequipped trade goods at a salvage bench",
    ),
    CommandSpec(
        "companion",
        ("mercenary", "hireling"),
        RecoveryClass.HARD,
        summary="companion [ADVISE|ORDER|SYNC|HIRE|DISMISS] - coordinate field support",
    ),
    CommandSpec(
        "party",
        ("group", "detail", "team"),
        RecoveryClass.HARD,
        summary=(
            "party [STATUS|FORM|REPORT|ORDER] - review or coordinate a "
            "bounded field detail"
        ),
    ),
    CommandSpec(
        "sovereignty",
        ("standing", "consequences"),
        summary="sovereignty [STATUS|RECORDS] - review chosen consequences and local trust",
    ),
    CommandSpec(
        "faction",
        ("factions", "realm", "realms"),
        RecoveryClass.HARD,
        summary="faction [STATUS|NAME] - review seven live relationships without silent enlistment",
    ),
    CommandSpec(
        "territory",
        ("sprawl", "community"),
        RecoveryClass.HARD,
        summary="territory [STATUS|SUPPORT SUPPLY|DEFENSE|RELIEF] - review or maintain Sprawl 15",
    ),
    CommandSpec(
        "civic",
        ("civicduty", "duty"),
        RecoveryClass.HARD,
        summary=(
            "civic [STATUS|ACCEPT|INSPECT|PLAN|EXECUTE|CLOSE] - complete "
            "one accountable Sprawl 15 duty"
        ),
    ),
    CommandSpec(
        "report",
        ("intel", "fieldreport"),
        RecoveryClass.HARD,
        summary=(
            "report [STATUS|CLASSIFY|VERIFY|TIMEBOX|QUARANTINE|MARK|PUBLISH|"
            "LENS|SEPARATE|TEST|LABEL|ANNOTATION] - review bounded evidence"
        ),
    ),
    CommandSpec(
        "district",
        ("passage", "districtpass"),
        RecoveryClass.HARD,
        summary=(
            "district [STATUS|ACCEPT|READ|PREPARE|VERIFY|CROSS] - review or "
            "complete a bounded public district passage"
        ),
    ),
    CommandSpec(
        "service",
        ("publicservice", "intake"),
        RecoveryClass.HARD,
        summary=(
            "service [STATUS|ACCEPT|READ|TRACE|COMPARE|SELECT|VERIFY|APPLY|"
            "CLOSE] - complete a bounded public-service review"
        ),
    ),
    CommandSpec(
        "hospice",
        ("capacity", "threshold"),
        RecoveryClass.HARD,
        summary=(
            "hospice [STATUS|ACCEPT|READ|INSPECT|TRACE|COMPARE|SELECT|VERIFY|APPLY|"
            "TEST|CLOSE] - manage bounded public-cell capacity and stewardship"
        ),
    ),
    CommandSpec(
        "appeal",
        ("correction", "recordreview"),
        RecoveryClass.HARD,
        summary=(
            "appeal [STATUS|ACCEPT|INSPECT|COMPARE|SELECT|VERIFY|APPLY|PUBLISH|"
            "TEST|CLOSE] - correct one bounded public-index label"
        ),
    ),
    CommandSpec(
        "wayfinding",
        ("waymark", "routesafety"),
        RecoveryClass.HARD,
        summary=(
            "wayfinding [STATUS|ACCEPT|INSPECT|COMPARE|SELECT|VERIFY|APPLY|"
            "TEST|CLOSE] - verify one bounded public route"
        ),
    ),
    CommandSpec("stance", (), RecoveryClass.HARD, summary="stance [NAME] - view or change posture"),
    CommandSpec("defense", ("defend",), RecoveryClass.HARD, summary="defense [MODE] - balanced, evade, block, or parry"),
    CommandSpec("stand", ("rise",), RecoveryClass.HARD, summary="stand - regain your feet after knockdown"),
    CommandSpec("attack", ("kill", "hit"), RecoveryClass.HARD, summary="attack [TARGET] - strike a foe"),
    CommandSpec("target", (), summary="target [FOE|clear] - set the default foe"),
    CommandSpec(
        "assess",
        ("consider",),
        summary="assess [FOE] - read relative danger and tactical pressure",
    ),
    CommandSpec("health", ("wounds",), summary="health - inspect injuries"),
    CommandSpec(
        "injury",
        ("difficulty", "curve", "survival"),
        summary="injury - inspect the level-band difficulty curve and recovery state",
    ),
    CommandSpec(
        "stabilize",
        ("staunch",),
        RecoveryClass.HARD,
        summary="stabilize [LOCATION] - slow bleeding; a patch kit seals it",
    ),
    CommandSpec("experience", ("exp",), summary="experience - view insight absorption"),
    CommandSpec(
        "train",
        ("skills",),
        RecoveryClass.HARD,
        summary="train [DISCIPLINE] - view or buy a generic training rank",
    ),
    CommandSpec(
        "retrain",
        ("respec", "refund"),
        RecoveryClass.HARD,
        summary="retrain [DISCIPLINE] - view or use an early rank refund",
    ),
    CommandSpec(
        "path",
        ("aptitude", "track"),
        RecoveryClass.HARD,
        summary="path [PROFILE] - view or choose one pre-training cost profile",
    ),
    CommandSpec(
        "plan",
        ("planner", "advancement"),
        summary="plan [DISCIPLINE] - preview costs, affordability, and next steps",
    ),
    CommandSpec(
        "course",
        ("lesson", "tutorial"),
        RecoveryClass.HARD,
        summary="course [START NAME|ABANDON] - view or manage an optional readiness course",
    ),
    CommandSpec(
        "talk",
        ("converse",),
        summary="talk NPC - speak with a person in the current room",
    ),
    CommandSpec(
        "choose",
        ("decide", "decision"),
        summary="choose OPTION - make the current story decision",
    ),
    CommandSpec(
        "interact",
        ("use", "activate"),
        RecoveryClass.HARD,
        summary="interact THING - act on a story object or mechanism",
    ),
    CommandSpec(
        "quest",
        ("objective", "mission"),
        summary="quest [RECORDS|RELATIONSHIPS] - review the active story",
    ),
    CommandSpec(
        "build",
        ("foundation",),
        summary=(
            "build [CLASS|AUTO|SET|RESET|TUTORIAL|CONFIRM] - "
            "configure a new character"
        ),
    ),
    CommandSpec(
        "guide",
        ("guidance",),
        summary="guide [START|SKIP] - manage optional contextual guidance",
    ),
    CommandSpec("info", ("score",), summary="info - view character summary"),
    CommandSpec(
        "effects",
        ("conditions",),
        summary="effects - view active injuries, posture, load, and recovery effects",
    ),
    CommandSpec("roundtime", ("rt",), summary="roundtime - show recovery time"),
    CommandSpec("search", (), RecoveryClass.HARD, summary="search - investigate the area"),
    CommandSpec("say", ("'",), summary="say MESSAGE - speak aloud"),
    CommandSpec("emote", ("pose",), summary="emote ACTION - describe an action"),
    CommandSpec("wait", (), summary="wait - check whether you have recovered"),
    CommandSpec(
        "rest",
        (),
        RecoveryClass.HARD,
        summary="rest - begin bounded health recovery in a safe room",
    ),
    CommandSpec(
        "recover",
        ("recall",),
        summary="recover - complete recovery after incapacitation becomes eligible",
    ),
    CommandSpec(
        "signal",
        ("callhelp",),
        summary="signal - persist a help request while incapacitated",
    ),
    CommandSpec("queue", ("qaction",), summary="queue [ACTION] - view or schedule one hard action"),
    CommandSpec("cancel", ("unqueue",), summary="cancel - remove the pending action"),
    CommandSpec("again", ("repeat", "g"), summary="again - repeat the last meaningful action"),
    CommandSpec("state", ("clientstate",), summary="state - emit structured client state"),
    CommandSpec("help", ("?", "commands"), summary="help [COMMAND] - learn commands"),
    CommandSpec("save", (), summary="save - explain automatic saving"),
    CommandSpec("quit", ("exit",), summary="quit - leave safely"),
)


class CommandParser:
    def __init__(self, specs: tuple[CommandSpec, ...] = DEFAULT_COMMANDS) -> None:
        self.specs = specs
        self._exact: dict[str, CommandSpec] = {}
        for spec in specs:
            for token in (spec.name, *spec.aliases):
                normalized = token.casefold()
                if normalized in self._exact:
                    raise ValueError(f"duplicate command token: {token}")
                self._exact[normalized] = spec

    def parse(self, raw: str) -> ParsedCommand:
        stripped = raw.strip()
        if not stripped:
            raise CommandParseError("Enter a command, or HELP for a command list.")
        if stripped.startswith("'") and len(stripped) > 1:
            return ParsedCommand(
                "say",
                (stripped[1:].lstrip(),),
                raw,
                RecoveryClass.SOFT,
            )
        tokens = self._tokenize(stripped)
        if not tokens:
            raise CommandParseError("Enter a command, or HELP for a command list.")
        verb = tokens[0].casefold()
        if verb in DIRECTIONS:
            return ParsedCommand(
                "go",
                (DIRECTIONS[verb],),
                raw,
                RecoveryClass.HARD,
            )
        spec = self._exact.get(verb)
        if spec is None:
            matches = {
                candidate
                for candidate in self.specs
                if len(verb) >= candidate.minimum_prefix
                and candidate.name.startswith(verb)
            }
            if len(matches) > 1:
                choices = ", ".join(sorted(match.name.upper() for match in matches))
                raise CommandParseError(f"That abbreviation is ambiguous: {choices}.")
            if len(matches) == 1:
                spec = matches.pop()
            else:
                candidates = sorted(set(self._exact) | set(DIRECTIONS))
                suggestion = difflib.get_close_matches(
                    verb, candidates, n=1, cutoff=0.68
                )
                if suggestion:
                    matched = suggestion[0]
                    if matched in DIRECTIONS:
                        corrected = DIRECTIONS[matched].upper()
                    else:
                        corrected = self._exact[matched].name.upper()
                    if len(tokens) > 1 and corrected not in {
                        direction.upper() for direction in DIRECTIONS.values()
                    }:
                        corrected = " ".join((corrected, *tokens[1:]))
                    raise CommandParseError(
                        f"I do not recognize {tokens[0]!r}. Did you mean {corrected}? "
                        "Use HELP HERE for exact commands available now."
                    )
                raise CommandParseError(
                    f"I do not recognize {tokens[0]!r}. "
                    "Try HELP, use NEXT for one exact step, or HELP HERE for nearby commands."
                )
        return ParsedCommand(
            name=spec.name,
            args=tuple(tokens[1:]),
            raw=raw,
            recovery=spec.recovery,
        )

    @staticmethod
    def _tokenize(value: str) -> list[str]:
        """Group quoted phrases while treating apostrophes inside words literally."""
        tokens: list[str] = []
        current: list[str] = []
        quote: str | None = None
        for character in value:
            if quote is not None:
                if character == quote:
                    quote = None
                else:
                    current.append(character)
                continue
            if character.isspace():
                if current:
                    tokens.append("".join(current))
                    current.clear()
                continue
            if character == '"' or (character == "'" and not current):
                quote = character
                continue
            current.append(character)
        if quote is not None:
            raise CommandParseError("I could not parse that: a quoted phrase is unfinished.")
        if current:
            tokens.append("".join(current))
        return tokens

    def spec_for(self, name: str) -> CommandSpec | None:
        return next((spec for spec in self.specs if spec.name == name), None)
