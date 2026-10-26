"""The console shell: navigation, tab behaviour and page chrome.

Chassis. Given a pack's departments, its view functions and the words that
describe what is being looked at, this renders the page and the tab machinery
around them. It never reads a figure.

The split matters because the tab behaviour is the part with bugs in it (deep
links beating remembered state, keyboard navigation, private-mode storage
throwing) and that is worth fixing once.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from fracture.pack.charts import TEMPLATES, Department, e


@dataclass(frozen=True)
class Chrome:
    """The words around the views. One per pack, built from its own data.

    `subjects` are pre-rendered rail lines because what identifies a subject is
    domain work: a firm is named with its AUM, an engagement with its fee and
    its stage.
    """

    title: str
    tenant_name: str
    period_label: str
    period_end: str
    generated_at: str
    subject_label: str
    subjects: tuple[str, ...]
    rail_note: str
    footer: str


def render(
    departments: Sequence[Department],
    views: Mapping[str, Callable[[Any], str]],
    data: Any,
    chrome: Chrome,
) -> str:
    missing = [d.key for d in departments if d.key not in views]
    if missing:
        raise KeyError(f"departments with no view function: {missing}")

    css = (TEMPLATES / "dashboard.css").read_text()

    nav = "".join(
        f'<button type="button" role="tab" id="tab-{d.key}" data-view="{d.key}" '
        f'aria-controls="view-{d.key}" aria-selected="{"true" if i == 0 else "false"}">'
        f'<span class="idx">{d.index}</span><span>{e(d.title)}</span></button>'
        for i, d in enumerate(departments)
    )

    sections = []
    for i, dept in enumerate(departments):
        body = views[dept.key](data)
        sections.append(
            f'<section class="view" id="view-{dept.key}" role="tabpanel" '
            f'aria-labelledby="tab-{dept.key}"{"" if i == 0 else " hidden"}>'
            '<header class="viewhead">'
            f"<h1>{e(dept.title)}</h1>"
            f'<p class="lede">{e(dept.lede)}</p>'
            f'<p class="owner">Owner: {e(dept.owner)} &middot; {e(chrome.period_label)} '
            f"{e(chrome.period_end)} &middot; {len(chrome.subjects)} "
            f"{e(chrome.subject_label)}</p>"
            "</header>"
            f"{body}"
            "</section>"
        )

    subject_lines = "".join(f"<div>{line}</div>" for line in chrome.subjects)

    return f"""<title>{e(chrome.title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>{css}</style>
<div class="shell">
  <aside class="rail">
    <div class="brand">
      <span class="mark">Fracture Systems</span>
      <span class="tenant">{e(chrome.tenant_name)}</span>
      <span class="sub">Operating console</span>
    </div>
    <nav class="deptnav" role="tablist" aria-label="Departments">{nav}</nav>
    <div class="rail-note">
      <div><strong>{e(chrome.period_label).capitalize()}</strong><br>{e(chrome.period_end)}</div>
      <div>{subject_lines}</div>
      <div>{e(chrome.rail_note)}</div>
    </div>
  </aside>
  <main class="main">
    {"".join(sections)}
    <p class="foot">Figures are current as at {e(chrome.generated_at)}Z, drawn from the same
    canonical model as the issued board pack and reconciled against each source system's own
    reported totals. {e(chrome.footer)} No figure on this page was computed by a model.</p>
  </main>
</div>
<script>
(function () {{
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.deptnav button'));
  var views = {{}};
  tabs.forEach(function (tab) {{
    views[tab.dataset.view] = document.getElementById('view-' + tab.dataset.view);
  }});

  function show(key, push) {{
    tabs.forEach(function (tab) {{
      var on = tab.dataset.view === key;
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
      if (views[tab.dataset.view]) views[tab.dataset.view].hidden = !on;
    }});
    try {{ localStorage.setItem('fracture.dept', key); }} catch (err) {{ /* private mode */ }}
    if (push && window.history && window.history.replaceState) {{
      window.history.replaceState(null, '', '#' + key);
    }}
    window.scrollTo(0, 0);
  }}

  tabs.forEach(function (tab, index) {{
    tab.addEventListener('click', function () {{ show(tab.dataset.view, true); }});
    tab.addEventListener('keydown', function (event) {{
      var next = null;
      if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = tabs[index + 1];
      if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = tabs[index - 1];
      if (next) {{ event.preventDefault(); next.focus(); show(next.dataset.view, true); }}
    }});
  }});

  // Deep link wins over the remembered tab, so a shared URL opens where the
  // sender meant rather than where the recipient last was.
  var fromHash = (window.location.hash || '').replace('#', '');
  var remembered = null;
  try {{ remembered = localStorage.getItem('fracture.dept'); }} catch (err) {{ remembered = null; }}
  var initial = views[fromHash] ? fromHash : (views[remembered] ? remembered : null);
  if (initial) show(initial, false);
}})();
</script>
"""


def write(
    departments: Sequence[Department],
    views: Mapping[str, Callable[[Any], str]],
    data: Any,
    chrome: Chrome,
    path: Path | str,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(departments, views, data, chrome), encoding="utf-8")
    return target
