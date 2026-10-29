"""Static guards for Qt code, run by every product over its own package.

Moved from BartleBot's ``test_theme_refresh_guard``, ``test_font_sizes``,
``test_button_guard`` and ``test_modal_guard``, with their reasons. Each function takes the
package to read and an allowlist, and returns a :class:`Report` (see
``bartleby.runtime.testing.guards``, which also says what an allowlist key may name). Keys
are relative to the package root passed in, so BartleBot reads ``bartlebot/gui`` and its
keys stay ``"settings/theme_tab.py:_reset_btn"``.

Parsed with ``ast`` where a regex would be fooled: several modules build stylesheets whose
text contains a ``QPushButton(``-shaped string, and a line-based regex cannot tell a
selector from a constructor.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from pathlib import Path

from bartleby.runtime.testing.guards import Finding, Report, module_name, parse, python_files, report

# ---------------------------------------------------------------------------
# Theme refresh
# ---------------------------------------------------------------------------

#: A call is "themed" when its argument mentions one of these. Several are indirect:
#: pill_style, group_accent, account_accent and account_tint resolve THEME internally,
#: so the call site names neither THEME nor a color.
THEME_MARKERS = (
    "THEME.",
    "pill_style(",
    "group_accent(",
    "region_color(",
    "account_accent(",
    "account_tint(",
)

#: Font sizes are tokens too, and they are *not* what the theme guard is about: every theme
#: carries the same ones, so a stylesheet that bakes in ``THEME.font_small_px`` shows the
#: right size after a switch, and there is nothing to re-derive. Stripped before the markers
#: are matched, so a widget is blamed for a color and not for a size.
#: ``test_no_font_token_varies_by_theme`` in core's theme tests is what keeps that true.
FONT_TOKEN = re.compile(r"THEME\.font_\w+")

#: Inheriting this is accepted in place of a hand-written changeEvent.
THEMED_MIXIN = "ThemedMixin"

THEME_ADVICE = (
    "They bake in a THEME-derived value but never re-derive it, so they show stale colors\n"
    "until the app restarts. Inherit ThemedMixin and implement apply_theme_styles(), or\n"
    "override changeEvent to re-apply on QEvent.Type.StyleChange. See bartleby/qt/CLAUDE.md,\n"
    '\'GUI theming\'. An allowlist entry\'s bar is "this should NEVER be fixed", not "not yet".'
)


def _called_names(node: ast.AST) -> set[str]:
    """Every function or method name called anywhere inside *node*."""
    out = set()
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        f = call.func
        out.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None))
    return out - {None}


def _direct_themed(node: ast.AST) -> str | None:
    """Name the way this call bakes in a THEME-derived value, or None."""
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)

    # ``set_style_sheet(w, sheet)`` is ``w.setStyleSheet(sheet)`` behind a skip-if-unchanged
    # check, so it bakes in a THEME value exactly the same way. It has to be named here or a
    # rename silently empties the guard: the helper's own body mentions no THEME marker, so
    # ``_themed_helpers`` cannot infer it either.
    if name in ("setStyleSheet", "set_style_sheet") and any(
        m in FONT_TOKEN.sub("", ast.unparse(node)) for m in THEME_MARKERS
    ):
        return f"{name}(THEME...)"

    if name == "svg_icon":
        tint = next((k.value for k in node.keywords if k.arg == "tint"), None)
        if tint is None:
            # Omitted tint is not "no tint": svg_icon falls back to THEME.icon_tint.
            return "svg_icon(tint defaults to THEME.icon_tint)"
        if any(m in ast.unparse(tint) for m in THEME_MARKERS):
            return "svg_icon(tint=THEME...)"
        # An explicit literal tint, or tint="", is theme-independent by choice.
    return None


def _themed_helpers(trees: dict[Path, ast.Module]) -> set[str]:
    """Module-level functions that bake in a THEME value, directly or via another.

    Matched by bare name across the package rather than resolved through imports: these are
    private helpers with distinctive names, and a name collision would only ever over-report,
    which fails loudly instead of silently.
    """
    helpers = {n.name: n for t in trees.values() for n in t.body if isinstance(n, ast.FunctionDef)}
    themed = {name for name, fn in helpers.items() if any(_direct_themed(s) for s in ast.walk(fn))}
    while True:
        grew = {n for n, fn in helpers.items() if n not in themed and (_called_names(fn) & themed)}
        if not grew:
            return themed
        themed |= grew


def _base_names(cls: ast.ClassDef) -> list[str]:
    return [n for b in cls.bases if (n := getattr(b, "id", getattr(b, "attr", None)))]


def _handles_theme_change(cls: ast.ClassDef, classes: dict[str, ast.ClassDef], _seen: set[str] | None = None) -> bool:
    """True if this class re-derives its styling when the theme switches.

    A ``changeEvent`` that never mentions ``StyleChange`` does not count: an empty override
    is how a half-finished fix looks, and it would otherwise silence the check for the class
    forever.

    Inheritance is followed through base classes defined in the package, because a card that
    subclasses a themed base and overrides ``apply_theme_styles`` is already correct; blaming
    it would push someone toward re-declaring the mixin on the subclass, and a mixin listed
    second aborts the process on a theme switch. Bases are matched by bare name, like
    ``_themed_helpers``: these are package-local classes, and a collision can only
    over-report.
    """
    if any(name == THEMED_MIXIN for name in _base_names(cls)):
        return True
    if any(
        isinstance(s, ast.FunctionDef) and s.name == "changeEvent" and "StyleChange" in ast.unparse(s)
        for s in ast.walk(cls)
    ):
        return True
    seen = _seen or {cls.name}
    for name in _base_names(cls):
        base = classes.get(name)
        if base is None or name in seen:
            continue
        if _handles_theme_change(base, classes, seen | {name}):
            return True
    return False


def theme_refresh_offenders(package_root: Path, allow: Mapping[str, str] | None = None) -> Report:
    """Every widget holding a THEME-derived value re-derives it on a theme switch.

    ``QApplication.setStyleSheet`` does not recompute a stylesheet a widget set on itself
    earlier, and ``svg_icon`` bakes its tint in at render time. So a value built from
    ``THEME`` at construction is a frozen snapshot of whatever theme was active then, and the
    widget shows stale colors until the app restarts. The fix is one ``changeEvent`` override
    per widget, or the mixin (``bartleby/qt/CLAUDE.md``, *GUI theming*).

    That convention was documented, had a worked code sample, and was on the PR checklist,
    and it was still missing from 22 of BartleBot's 43 themed classes when this guard was
    written. The convention was never the hard part; *noticing* was, because noticing meant a
    human switching themes and eyeballing every panel. This is that human, on every commit.

    Two things make it catch more than a grep would:

    * **Helpers are transparent.** ``_apply_list_style(w)`` styles a widget it was handed,
      and ``field_label(text)`` returns one it built. Neither can re-style anything on a
      theme switch; the *caller* owns that instance and is the one that has to re-apply. So a
      themed helper propagates to its callers, to a fixed point, and the class that gets
      blamed is the class that can fix it.
    * **A bare ``svg_icon(name)`` is themed.** Its ``tint`` defaults to ``THEME.icon_tint``,
      so an icon set once and kept goes stale exactly like a stylesheet, with no stylesheet
      anywhere for a text search to find.

    Widgets that read ``THEME`` at paint time need none of this and are invisible here: they
    hold no baked value. Keyed ``"<module>:<ClassName>"``.
    """
    trees = {p: parse(p) for p in python_files(package_root)}
    helpers = _themed_helpers(trees)
    classes = {n.name: n for t in trees.values() for n in ast.walk(t) if isinstance(n, ast.ClassDef)}
    findings = []
    for path, tree in trees.items():
        module = module_name(path, package_root)
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            reasons = sorted({r for s in ast.walk(cls) if (r := _direct_themed(s))})
            reasons += [f"calls {h}()" for h in sorted(_called_names(cls) & helpers)]
            if reasons and not _handles_theme_change(cls, classes):
                findings.append(Finding(f"{module}:{cls.name}", module, cls.lineno, "; ".join(reasons)))
    return report(findings, allow or {}, THEME_ADVICE)


def mixin_misuse(package_root: Path) -> list[str]:
    """Every ``ThemedMixin`` user lists it first, implements its hook, and applies it at construction.

    Three mistakes, none of which can be caught at run time:

    * **The mixin after a Qt base.** Python resolves ``changeEvent`` left to right, so
      ``class P(QWidget, ThemedMixin)`` does not merely skip the mixin: sending a StyleChange
      to such a widget aborts the process inside ``sendEvent``, with no exception and no
      traceback.
    * **No ``apply_theme_styles``.** The mixin's own error is raised at run time, and
      swallowed by ``changeEvent``.
    * **No ``refresh_theme_styles()`` outside ``changeEvent``.** A widget styled only on the
      *next* theme switch is unstyled until then. The mixin cannot do this itself: when any
      ``__init__`` it could hook into runs, the children ``apply_theme_styles`` is about to
      style do not exist yet. Known gap: this checks that the call *exists*, not that
      construction reaches it. BartleBot's ``WikiImportDialog`` passed while its only caller
      was ``_set_status``, which ``__init__`` never runs, so the dialog opened unstyled. The
      behavioral tests per widget are what close this.

    Returns one line per mistake, ``"<module>:<ClassName>: <what>"``.
    """
    problems = []
    for path in python_files(package_root):
        module = module_name(path, package_root)
        for cls in (n for n in ast.walk(parse(path)) if isinstance(n, ast.ClassDef)):
            names = [getattr(b, "id", getattr(b, "attr", None)) for b in cls.bases]
            if THEMED_MIXIN not in names:
                continue
            where = f"{module}:{cls.name}"
            if names.index(THEMED_MIXIN) != 0:
                problems.append(f"{where}: ThemedMixin must come first ({', '.join(n or '?' for n in names)})")
            if not any(isinstance(s, ast.FunctionDef) and s.name == "apply_theme_styles" for s in ast.walk(cls)):
                problems.append(f"{where}: inherits ThemedMixin but never implements apply_theme_styles")
            outside = [
                s
                for s in cls.body
                if not (isinstance(s, ast.FunctionDef) and s.name in ("changeEvent", "apply_theme_styles"))
            ]
            if not any("refresh_theme_styles" in _called_names(s) for s in outside):
                problems.append(f"{where}: never calls self.refresh_theme_styles() outside changeEvent")
    return problems


# ---------------------------------------------------------------------------
# Font sizes
# ---------------------------------------------------------------------------

#: (what, pattern, what to do instead)
FONT_RULES: list[tuple[str, str, str]] = [
    (
        "literal font-size in a style sheet",
        r"font-size:\s*\d",
        'use a token: f"font-size: {THEME.font_small_px}px"',
    ),
    (
        "font size set in points",
        r"\.setPointSizeF?\(",
        "sizes are px: use setPixelSize() with a token, or THEME.scaled_ui_font()",
    ),
    (
        "literal pixel size",
        r"\.setPixelSize\(\s*\d",
        "use a token, or an offset from one: THEME.font_body_px + 1",
    ),
    (
        "literal size in a QFont constructor",
        r"QFont\([^()]*,\s*\d",
        "use THEME.ui_font(px, bold=...)",
    ),
    (
        "font size read back off a font",
        r"\.(pointSizeF?|pixelSize)\(\)",
        "set the size from a token; a px font reports pointSize() as -1",
    ),
    (
        "per-module font size constant",
        r"^_?[A-Z_]*FONT_(PX|PT|SIZE)\b\s*=|^\s*[A-Z_]*FONT_(PX|PT|SIZE)\b\s*=\s*\d",
        "the token belongs in bartleby/qt/theming/theme.py",
    ),
]


def literal_font_sizes(package_root: Path, allow: Mapping[str, str] | None = None) -> Report:
    """Every font size comes from a ``THEME`` token.

    Sizes were written as literals in about eighty places in BartleBot once (``font-size:
    11px`` in a style sheet, ``setPointSize(9)`` on a font, a ``_FONT_PX`` constant per
    module), so the same decision was kept in dozens of places and drifted into six values
    doing four jobs. They are tokens now, and this holds the line.

    The reads are barred for a separate reason. ``pointSize()`` returns -1 on a font whose
    size was set in px, so ``font.setPointSize(font.pointSize() + 1)`` silently resolves to
    0, and a ``pointSizeF()`` read into a ``max(..., 6.0)`` floor silently resolves to that
    floor. Both shipped before the conversion. Size a font from a token instead of from
    another font.

    A comment line is prose about the rule, not a size, and is skipped. The module that
    defines the tokens is exempt by its allowlist entry. Keyed ``"<module>:<line>"``.
    """
    rules = [(what, re.compile(pattern, re.MULTILINE), instead) for what, pattern, instead in FONT_RULES]
    findings = []
    for path in python_files(package_root):
        module = module_name(path, package_root)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for what, rx, instead in rules:
                if rx.search(line):
                    findings.append(
                        Finding(f"{module}:{number}", module, number, f"{what} ({instead}): {line.strip()}")
                    )
    return report(findings, allow or {}, "Size every font from a THEME token (bartleby/qt/CLAUDE.md, 'Font sizes').")


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------

BUTTON_ADVICE = (
    "Use ActionButton for a labeled verb sharing a row, IconButton for a glyph with no label,\n"
    "or LinkButton for a control inline in prose or under a card (bartleby/qt/CLAUDE.md,\n"
    "'Buttons'). If it is genuinely none of those, allowlist it with a reason: the reason is\n"
    'the deliverable, not the exemption. The bar is "this is not a labeled verb sharing a\n'
    'row": a button whose background is the data it displays, or one that belongs to another\n'
    'vocabulary (a status pill). "It would be fiddly to convert" is not a reason.'
)


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def _bound_name(stmt: ast.stmt) -> str | None:
    """The name a statement binds, for ``x = ...``, ``self._x = ...`` and annotated forms."""
    targets: list[ast.expr] = []
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
    elif isinstance(stmt, ast.AnnAssign):
        targets = [stmt.target]
    for t in targets:
        if isinstance(t, ast.Name):
            return t.id
        if isinstance(t, ast.Attribute):
            return t.attr
    return None


def bare_buttons(package_root: Path, allow: Mapping[str, str] | None = None) -> Report:
    """Every ``QPushButton`` is one of the three button classes, or allowlisted.

    ``ActionButton``, ``IconButton`` and ``LinkButton`` are the family's three button
    classes, and which one a widget is is a decision with a documented reason behind it.
    Fusion's default chrome is a beveled frame sized to its own label: it flattens the SVG
    icons drawn against it, and a row of such buttons sizes each one to its own text instead
    of sharing the row. That is what the three classes exist to stop, and a bare
    ``QPushButton`` is how it comes back.

    Direct instantiation only. ``IconButton`` and ``LinkButton`` both subclass
    ``QPushButton``, and they are the sanctioned answer for the buttons that are not labeled
    verbs. Keyed ``"<module>:<the name it is bound to>"``, so an edit above an entry does not
    invalidate it, and so the key says what the button is. A call never bound to a name is
    keyed off its line, ``"<module>:<line N>"``, so it cannot slip past by being written
    inline; that key moves when the file does, which is the nudge to give it a name.
    """
    findings = []
    for path in python_files(package_root):
        module = module_name(path, package_root)
        tree = parse(path)
        named: dict[int, str] = {}
        for stmt in ast.walk(tree):
            if not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or stmt.value is None:
                continue
            name = _bound_name(stmt)
            if name is None:
                continue
            for call in (n for n in ast.walk(stmt.value) if isinstance(n, ast.Call)):
                if _call_name(call) == "QPushButton":
                    named[call.lineno] = name
        seen = set()
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            if _call_name(call) != "QPushButton":
                continue
            key = f"{module}:{named.get(call.lineno, f'<line {call.lineno}>')}"
            if key not in seen:
                seen.add(key)
                findings.append(Finding(key, module, call.lineno, "bare QPushButton"))
    return report(sorted(findings, key=lambda f: f.key), allow or {}, BUTTON_ADVICE)


#: The modules that *define* the weights rather than spend them: a branch of the styling
#: code, and a parameter default. Neither is a button on a surface.
VOCABULARY_MODULES = frozenset({"widgets/buttons/action_button.py", "widgets/layout/dialog_footer.py"})


def extra_primaries(package_root: Path, allow: Mapping[str, str] | None = None) -> Report:
    """At most one ``ActionStyle.PRIMARY`` per module.

    ``PRIMARY`` is "the one thing this surface expects you to do", so, one. A second one
    always looks locally reasonable, which is exactly why the rule needs a test rather than a
    paragraph. Module is the proxy for surface; where a module really does hold two surfaces,
    allowlist it naming both. A ``SECONDARY`` with no ``PRIMARY`` beside it is legal. The
    modules in :data:`VOCABULARY_MODULES` (by their path under a ``gui`` or ``qt`` root) are
    not counted. Keyed by module.
    """
    findings = []
    for path in python_files(package_root):
        module = module_name(path, package_root)
        if any(module.endswith(v) for v in VOCABULARY_MODULES):
            continue
        lines = [
            node.lineno
            for node in ast.walk(parse(path))
            if isinstance(node, ast.Attribute)
            and node.attr == "PRIMARY"
            and isinstance(node.value, ast.Name)
            and node.value.id == "ActionStyle"
        ]
        if len(lines) > 1:
            findings.append(Finding(module, module, lines[1], f"ActionStyle.PRIMARY on lines {lines}"))
    return report(
        findings,
        allow or {},
        "Demote the others to SECONDARY (a way forward that is not the commit) or TERTIARY.",
    )


#: Selectors that make ``QStyleSheetStyle`` take over a compound widget, and why each hurts.
#: Matched against stylesheet *strings*, so an ``isinstance(w, QAbstractSpinBox)`` in ordinary
#: code is invisible here, correctly, since it styles nothing.
FORBIDDEN_SELECTORS: dict[str, str] = {
    "QAbstractSpinBox": (
        "A border on it makes QStyleSheetStyle take over the whole control and the up/down "
        "buttons stop being drawn at all. Leave the stepper with the style's own frame."
    ),
    "QSpinBox": "Same takeover as QAbstractSpinBox: the steppers stop being drawn at all.",
    "QDoubleSpinBox": "Same takeover as QAbstractSpinBox: the steppers stop being drawn at all.",
    "QComboBox::drop-down": (
        "The arrow becomes ours to draw the moment this is set, and the theme's arrow is an SVG "
        "rendered per theme. Style the combo box in theme.py or not at all."
    ),
    "QCheckBox::indicator": "Qt stops drawing the check mark and the box, and there is no art for either.",
    "QRadioButton::indicator": "Qt stops drawing the dot and the ring, and there is no art for either.",
    "QScrollBar": (
        "One rule anywhere on a scrollbar hands the whole control over, arrows included. theme.py styles them globally."
    ),
}

#: A QSS comment is prose about a rule, not a rule.
_QSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def compound_widget_qss(package_root: Path, allow: Mapping[str, str] | None = None) -> Report:
    """No stylesheet targets a compound widget's subcontrols.

    Setting any property on one of these hands the whole control to ``QStyleSheetStyle``,
    which then draws only what the stylesheet describes: a spinbox loses its steppers, a
    check box its check mark, a scrollbar its arrows. Nothing fails and nothing logs; the
    parts are simply not there any more. The one way a styling change breaks *rendering*
    rather than looks. The module that owns the global stylesheet is exempt by its allowlist
    entry. Docstrings are skipped, because prose about a selector is not a rule using it.
    Keyed ``"<module>:<line>"``.
    """
    findings = []
    for path in python_files(package_root):
        module = module_name(path, package_root)
        tree = parse(path)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docstrings:
                continue
            body = _QSS_COMMENT.sub("", node.value)
            for selector, why in FORBIDDEN_SELECTORS.items():
                if selector in body:
                    findings.append(Finding(f"{module}:{node.lineno}", module, node.lineno, f"{selector}: {why}"))
    return report(findings, allow or {}, "Only the module that owns the global stylesheet may style these.")


# ---------------------------------------------------------------------------
# Modals in coroutines
# ---------------------------------------------------------------------------

#: The statics that open a modal.
MODAL_CLASSES = frozenset({"QMessageBox", "QFileDialog", "QInputDialog", "QColorDialog", "QFontDialog"})


def _own_nodes(fn: ast.AsyncFunctionDef):
    """Every node in *fn*'s body, less what a lambda or nested function inside it holds."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _is_modal(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in ("exec", "exec_"):
        return True
    return isinstance(func.value, ast.Name) and func.value.id in MODAL_CLASSES


def modals_in_coroutines(package_root: Path, allow: Mapping[str, str] | None = None) -> Report:
    """No coroutine opens a modal itself (BartleBot #154).

    A coroutine runs as an asyncio task, and a modal opened inside one (``exec()``, or a
    ``QMessageBox``/``QFileDialog``/``QInputDialog`` static) re-enters Qt's event loop with
    the task still on the stack. Every other task that wakes while it is up is wedged for
    good (the qasync re-entrancy rule in ``bartleby/qt/CLAUDE.md``). A coroutine defers the
    dialog instead: ``QTimer.singleShot(0, lambda: ...)``, a queued signal, or
    ``QMenu.popup``.

    A call inside a lambda or a nested function is not the coroutine's own, and is allowed:
    that is what a deferral looks like. Keyed ``"<module>:<coroutine>"``.
    """
    findings = []
    for path in python_files(package_root):
        module = module_name(path, package_root)
        for fn in ast.walk(parse(path)):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for node in _own_nodes(fn):
                if isinstance(node, ast.Call) and _is_modal(node):
                    findings.append(Finding(f"{module}:{fn.name}", module, node.lineno, ast.unparse(node.func)))
    return report(
        findings, allow or {}, "Defer the dialog: QTimer.singleShot(0, ...), a queued signal, or QMenu.popup."
    )
