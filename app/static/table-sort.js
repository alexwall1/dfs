/**
 * Client-side table sorting.
 * Add class "sortable" to any <table> to enable.
 * Clicking a <th> sorts that column; clicking again reverses.
 */
(function () {
  function cellText(row, colIndex) {
    const cell = row.cells[colIndex];
    return cell ? cell.innerText.trim() : '';
  }

  function compareValues(a, b, asc) {
    // ISO date (YYYY-MM-DD) sorts correctly as string, but let's be explicit
    const dateRe = /^\d{4}-\d{2}-\d{2}$/;
    let cmp;
    if (dateRe.test(a) && dateRe.test(b)) {
      cmp = a < b ? -1 : a > b ? 1 : 0;
    } else {
      const numA = parseFloat(a.replace(/\s/g, '').replace(',', '.'));
      const numB = parseFloat(b.replace(/\s/g, '').replace(',', '.'));
      if (!isNaN(numA) && !isNaN(numB)) {
        cmp = numA - numB;
      } else {
        cmp = a.localeCompare(b, 'sv');
      }
    }
    return asc ? cmp : -cmp;
  }

  function initTable(table) {
    const headers = table.querySelectorAll('thead th');
    headers.forEach(function (th, colIndex) {
      th.style.cursor = 'pointer';
      th.style.userSelect = 'none';
      th.setAttribute('aria-sort', 'none');

      const indicator = document.createElement('span');
      indicator.className = 'sort-indicator ms-1 text-muted';
      indicator.setAttribute('aria-hidden', 'true');
      indicator.textContent = '⇅';
      th.appendChild(indicator);

      th.addEventListener('click', function () {
        const asc = th.getAttribute('aria-sort') !== 'ascending';

        // Reset all headers
        headers.forEach(function (h) {
          h.setAttribute('aria-sort', 'none');
          h.querySelector('.sort-indicator').textContent = '⇅';
          h.querySelector('.sort-indicator').classList.remove('text-dark');
          h.querySelector('.sort-indicator').classList.add('text-muted');
        });

        th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');
        indicator.textContent = asc ? '↑' : '↓';
        indicator.classList.remove('text-muted');
        indicator.classList.add('text-dark');

        const tbody = table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));

        // Skip "no results" rows (single colspan cell)
        if (rows.length === 1 && rows[0].cells.length === 1) return;

        rows.sort(function (ra, rb) {
          return compareValues(cellText(ra, colIndex), cellText(rb, colIndex), asc);
        });

        rows.forEach(function (row) { tbody.appendChild(row); });
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('table.sortable').forEach(initTable);
  });
})();
