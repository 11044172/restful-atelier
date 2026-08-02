import type { Product } from './types';

export const products: Product[] = [
  { id: 'morning-cup', maker: '靜院晨物', name: '素胎粗陶・晨飲杯', price: 1280, imageLabel: 'MORNING CUP / FRONT', tone: 'clay', category: '手作', label: '限量 12 件' },
  { id: 'wood-spoons', maker: '靜院器用', name: '原木長柄湯匙・三支組', price: 880, imageLabel: 'SPOON TRIO / ASH WOOD', tone: 'wood', category: '木作' },
  { id: 'linen-cloth', maker: '靜院布織', name: '亞麻粗織・桌巾（薄霧）', price: 2480, imageLabel: 'LINEN / MIST', tone: 'linen', category: '織品' },
  { id: 'round-vase', maker: '靜院選物', name: '陶製花器・圓口', price: 3680, imageLabel: 'VASE / ROUND MOUTH', tone: 'stone', category: '限量', label: '一件入荷' },
  { id: 'cotton-blanket', maker: '靜院居織', name: '手織棉毯・霧雅灰', price: 4200, imageLabel: 'WOVEN BLANKET / GREY', tone: 'fog', category: '織品' },
  { id: 'tea-trays', maker: '靜院茶具', name: '柚木茶托・四件組', price: 1580, imageLabel: 'TEAK TRAYS / SET OF 4', tone: 'tea', category: '木作' },
  { id: 'candle-holder', maker: '靜院器物', name: '黃銅燭台・低座', price: 2280, imageLabel: 'BRASS / LOW LIGHT', tone: 'brass', category: '手作' },
  { id: 'bamboo-basket', maker: '靜院居所', name: '竹編提籃・方口', price: 1980, imageLabel: 'BAMBOO BASKET / SQUARE', tone: 'bamboo', category: '限量', label: '限量 8 件' },
];

export const formatPrice = (value: number) => `NT$ ${value.toLocaleString('zh-TW')}`;
