export interface LearnCatalog {
  notebooks: LearnNotebook[];
}

export interface LearnNotebook {
  name: string;
  path: string;
  slug: string;
  title: string;
  description?: string;
  topic: string;
  cell_count: number;
}

export interface TopicGroup {
  topic: string;
  title: string;
  notebooks: LearnNotebook[];
}

export interface CatalogStats {
  total: number;
  topics: number;
  cells: number;
}
