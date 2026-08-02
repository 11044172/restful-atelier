export const site = {
  collection: {
    year: 2026,
    season: '夏',
    label: '2026 夏',
    name: '清歡',
    homeEyebrow: '靜院選集',
    shopEyebrow: '新季',
    announcement: '消費滿 NT$ 2,000 免運・三十日內可退換',
  },
  name: '靜院居家',
  english: 'RESTFUL ATELIER',
  motto: '靜處安身，院宅清歡',
  belief: '自己是自己的典範。',
  description: '室內設計、生活器物與閱讀提案，整理屬於自己的生活節奏。',
  address: '台北市中山區某某路 168 號 2F',
  hours: '週三至週六・13:00 — 19:00',
  about: [
    '從 2019 年第一個住宅案開始，我們相信家不只是空間，而是一個人對自己最誠實的投影。',
    '所以我們從規劃動線開始，也從挑選一只茶杯開始——因為每一件進入你家的東西，都會和你一起呼吸。',
    '這也是為什麼，在靜院，設計與選物從不分開。',
  ],
};

export const withBase = (path = '') => {
  const base = import.meta.env.BASE_URL;
  return `${base}${path.replace(/^\//, '')}`;
};
