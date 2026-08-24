/**
 * RecoveryOS Dashboard — Minimal Client-side Search & Filter
 */

document.addEventListener('DOMContentLoaded', () => {
  const searchInput = document.getElementById('tableSearchInput');
  const table = document.getElementById('casesTable');

  if (searchInput && table) {
    searchInput.addEventListener('keyup', () => {
      const filter = searchInput.value.toLowerCase();
      const rows = table.getElementsByTagName('tr');

      for (let i = 1; i < rows.length; i++) {
        const row = rows[i];
        const text = row.textContent.toLowerCase();
        if (text.includes(filter)) {
          row.style.display = '';
        } else {
          row.style.display = 'none';
        }
      }
    });
  }
});
