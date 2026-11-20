"""动态契约扫描器：抽取页面常驻交互控件，生成契约与交互指纹

设计要点：
- 不依赖前端 Schema，直接扫描真实 DOM
- 过滤不可见控件、临时 toast / 浮层
- 指纹只基于「控件集合」的稳定签名，页面结构变更即可感知
"""
import hashlib
import json

from playwright.async_api import Page

SCAN_JS = r"""
() => {
  function stableSelector(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    const name = el.getAttribute('name');
    if (name) return el.tagName.toLowerCase() + '[name="' + name + '"]';
    const dataTest = el.getAttribute('data-testid') || el.getAttribute('data-test');
    if (dataTest) return '[data-testid="' + dataTest + '"]';
    const cls = (el.className && typeof el.className === 'string')
      ? el.className.split(/\s+/).filter(Boolean).slice(0, 2).join('.') : '';
    return el.tagName.toLowerCase() + (cls ? '.' + cls : '');
  }

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    if (parseFloat(style.opacity || '1') === 0) return false;
    return true;
  }

  function nearbyLabel(el) {
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l && l.innerText.trim()) return l.innerText.trim().slice(0, 40);
    }
    const wrap = el.closest('label');
    if (wrap && wrap.innerText.trim()) return wrap.innerText.trim().slice(0, 40);
    let prev = el.previousElementSibling;
    for (let i = 0; prev && i < 3; i++) {
      const t = (prev.innerText || '').trim();
      if (t && t.length <= 40) return t;
      prev = prev.previousElementSibling;
    }
    let par = el.parentElement;
    for (let i = 0; par && i < 3; i++) {
      const t = (par.innerText || '').trim();
      if (t && t.length <= 40) return t;
      par = par.parentElement;
    }
    return '';
  }

  const nodes = document.querySelectorAll('input, select, textarea, button, a[role="button"]');
  const items = [];
  for (const el of nodes) {
    if (!isVisible(el)) continue;
    const rect = el.getBoundingClientRect();
    // 过滤屏幕外的隐藏控件 / 极小浮层
    if (rect.width < 8 || rect.height < 8) continue;
    items.push({
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      name: el.getAttribute('name') || '',
      selector: stableSelector(el),
      label: (el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('aria-label') || '').trim().slice(0, 60),
      nearby_label: nearbyLabel(el),
      options: el.tagName === 'SELECT' ? Array.from(el.options || []).map(o => (o.text || '').trim()).slice(0, 20) : [],
      required: el.hasAttribute('required') || el.getAttribute('aria-required') === 'true',
    });
  }
  return {
    url: location.href,
    title: document.title,
    controls: items
  };
}
"""

# 指纹计算只保留「结构签名」字段，剔除 label 文案抖动
def _fingerprint_payload(controls: list[dict]) -> str:
    sig = [
        {
            "tag": c["tag"],
            "type": c["type"],
            "name": c["name"],
            "selector": c["selector"],
        }
        for c in controls
    ]
    return json.dumps(sig, sort_keys=True, ensure_ascii=False)


async def scan_contract(page: Page) -> dict:
    """返回 {"contract": {...}, "fingerprint": "..."}"""
    try:
        result = await page.evaluate(SCAN_JS)
    except Exception:
        result = {"url": page.url, "title": "", "controls": []}

    controls = result.get("controls", [])
    raw = _fingerprint_payload(controls)
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    return {
        "contract": result,
        "fingerprint": fingerprint,
    }
