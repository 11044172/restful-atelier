export type Product = {
  id: string;
  maker: string;
  name: string;
  price: number;
  label?: string;
  imageLabel: string;
  tone: string;
  category: '手作' | '木作' | '織品' | '限量';
};

export type Project = {
  id: string;
  title: string;
  english: string;
  year: string;
  style: string;
  size: string;
  imageLabel: string;
  tone: string;
  group: '風格' | '諮詢' | '接案';
};

export type Publication = {
  number: string;
  pages: string;
  title: string;
  subtitle: string;
  tone: string;
};

export type Category = {
  name: string;
  count: number;
  label: string;
  tone: string;
};
