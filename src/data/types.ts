export type CategorySlug =
  | 'daily-living'
  | 'incense-fragrance'
  | 'tea-selection'
  | 'art-collection'
  | 'healthy-living'
  | 'pampered-pets';

export type Product = {
  slug: string;
  maker: string;
  series: string;
  name: string;
  price: number;
  featuredOrder: number;
  publishedAt: string;
  label?: string;
  imageLabel: string;
  tone: string;
  categorySlug: CategorySlug;
  subcategory: string;
  description: string;
  material: string;
  dimensions: string;
  origin: string;
  stock: string;
  care: string;
  shipping: string;
  makerStory: string;
};

export type ProjectType = '住宅空間' | '商業空間' | '民宿旅宿' | '老屋改造';

export type Project = {
  slug: string;
  title: string;
  english: string;
  year: string;
  location: string;
  style: string;
  size: string;
  projectType: ProjectType;
  imageLabel: string;
  tone: string;
  concept: string;
  conceptTitle: string;
  designNotes: string[];
  materials: string[];
  gallery: { label: string; tone: string }[];
};

export type Publication = {
  number: string;
  pages: string;
  title: string;
  subtitle: string;
  tone: string;
};

export type Category = {
  slug: CategorySlug;
  name: string;
  label: string;
  tone: string;
  description: string;
  subcategories: string[];
};
