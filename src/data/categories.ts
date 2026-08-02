import type { Category } from './types';

export const categories: Category[] = [
  { slug: 'daily-living', name: '居家生活', label: 'HOME LIVING', tone: 'linen', description: '從早晨的一杯水到深夜的一方織物，收錄讓日常慢慢安定下來的器物。', subcategories: ['陶器', '織品'] },
  { slug: 'incense-fragrance', name: '香道香氛', label: 'INCENSE', tone: 'smoke', description: '以一縷氣味整理房間，也替心緒留出一段安靜的空白。', subcategories: ['香器', '燭具'] },
  { slug: 'tea-selection', name: '茶道茶品', label: 'TEA', tone: 'tea', description: '從沏茶到入席，選擇經得起每日使用、也能與時間相處的茶器。', subcategories: ['茶器', '收納'] },
  { slug: 'art-collection', name: '藝術骨董', label: 'ART & ANTIQUES', tone: 'ink', description: '不急著填滿空間，只留下能被長久觀看、與生活互相照映的作品。', subcategories: ['花器', '擺件'] },
  { slug: 'healthy-living', name: '樂齡樂活', label: 'SLOW AGING', tone: 'moss', description: '讓身體自在、動作從容，以溫潤材質陪伴每一段生活節奏。', subcategories: ['餐具', '起居'] },
  { slug: 'pampered-pets', name: '寵愛寵物', label: 'WITH PETS', tone: 'clay', description: '為一同生活的夥伴選擇安全、耐用，也能安靜融入居家的日常用品。', subcategories: ['食器', '休憩'] },
];

export const getCategory = (slug: string) => categories.find((category) => category.slug === slug);
