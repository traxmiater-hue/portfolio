// клик по строке таблицы открывает заявку
document.querySelectorAll('tr.click').forEach(tr =>
  tr.addEventListener('click', e => { if (!e.target.closest('a')) location.href = tr.dataset.href; }));

// подтверждение опасных действий
document.querySelectorAll('[data-confirm]').forEach(b =>
  b.addEventListener('click', e => { if (!confirm(b.dataset.confirm)) e.preventDefault(); }));

// канбан: перетаскивание карточек между колонками
const csrf = document.querySelector('meta[name=csrf]')?.content;
let dragged = null;
document.querySelectorAll('.kcard').forEach(card => {
  card.addEventListener('dragstart', e => { dragged = card; card.classList.add('drag'); e.dataTransfer.effectAllowed = 'move'; });
  card.addEventListener('dragend', () => { card.classList.remove('drag'); dragged = null; });
});

function recount() {
  document.querySelectorAll('.col').forEach(c => c.querySelector('.count').textContent = c.querySelectorAll('.kcard').length);
}

document.querySelectorAll('.col').forEach(col => {
  col.addEventListener('dragover', e => { e.preventDefault(); col.classList.add('over'); });
  col.addEventListener('dragleave', e => { if (!col.contains(e.relatedTarget)) col.classList.remove('over'); });
  col.addEventListener('drop', async e => {
    e.preventDefault();
    col.classList.remove('over');
    if (!dragged || dragged.closest('.col') === col) return;
    const from = dragged.closest('.cards'), card = dragged;
    col.querySelector('.cards').prepend(card);
    recount();
    const r = await fetch(`/api/leads/${card.dataset.id}/status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf },
      body: JSON.stringify({ status: col.dataset.status })
    }).catch(() => null);
    if (!r || !r.ok) { from.prepend(card); recount(); alert('Не удалось сменить статус. Обновите страницу.'); }
  });
});
