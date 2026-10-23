export default function Sidebar({ conversations, activeId, loadingIds, open, onCreate, onSelect, onDelete, onClose }) {
  function handleDelete(e, conv) {
    e.stopPropagation()
    if (window.confirm(`Удалить диалог «${conv.title}»?`)) onDelete(conv.id)
  }

  return (
    <>
      {open && <div className="scrim" onClick={onClose} />}
      <aside className={`sidebar ${open ? 'sidebar-open' : ''}`} aria-label="Список диалогов">
        <button className="new-chat" onClick={onCreate}>
          + Новый диалог
        </button>
        <ul className="dialogs">
          {conversations.map((c) => (
            <li key={c.id}>
              <div
                className={`dialog ${c.id === activeId ? 'dialog-active' : ''}`}
                role="button"
                tabIndex={0}
                onClick={() => onSelect(c.id)}
                onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect(c.id)}
              >
                <span className="dialog-title">{c.title}</span>
                {loadingIds.includes(c.id) && <span className="dialog-busy" aria-label="Идёт ответ" />}
                <button className="dialog-delete" aria-label={`Удалить диалог ${c.title}`} onClick={(e) => handleDelete(e, c)}>
                  ×
                </button>
              </div>
            </li>
          ))}
        </ul>
      </aside>
    </>
  )
}
