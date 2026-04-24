// TeleScribe admin API client.
// Подменяет window.MOCK.* реальными данными из /admin/api/* и шлёт
// 'mock-updated' event, чтобы App-компонент форс-ререндерился.
// Загружается после mockData.jsx — моки остаются fallback'ом если сеть упала.

window.TS_API = {
  async listMeetings(params = {}) {
    const qs = new URLSearchParams(params).toString();
    const url = '/admin/api/meetings' + (qs ? '?' + qs : '');
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error('listMeetings ' + resp.status);
    return resp.json();
  },
  async getMeeting(id) {
    const resp = await fetch('/admin/api/meetings/' + encodeURIComponent(id), {
      credentials: 'same-origin',
    });
    if (!resp.ok) throw new Error('getMeeting ' + resp.status);
    return resp.json();
  },
};

(async () => {
  try {
    const data = await window.TS_API.listMeetings({ limit: 200 });
    if (Array.isArray(data.items)) {
      window.MOCK.MEETINGS = data.items;
      window.dispatchEvent(new Event('mock-updated'));
      console.info('TeleScribe: loaded', data.items.length, 'meetings from API');
    }
  } catch (err) {
    console.warn('TeleScribe: API load failed, keeping mock data:', err);
  }
})();
