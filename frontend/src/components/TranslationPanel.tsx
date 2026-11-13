/**
 * 中英对照翻译面板：UI 占位（置灰），待接入 POST /api/translate/*。
 */
export default function TranslationPanel() {
  return (
    <footer className="translation-panel" aria-disabled>
      <div className="translation-head">
        <span className="translation-title">中英对照翻译</span>
        <span className="translation-badge">待接入</span>
      </div>
      <p className="translation-hint">
        逐段中英对照翻译将在此展示 · 后端接口待接入 <code>POST /api/translate/*</code>
      </p>
    </footer>
  )
}
