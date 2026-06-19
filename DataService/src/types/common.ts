export type DataSource = 'DataService';

export interface ResponseMeta {
  source: DataSource;
  provider: string | null;
  fallback: boolean;
  cached: boolean;
  stale: boolean;
  updatedAt: string;
}

export interface ServiceResult<T> {
  data: T;
  provider: string;
  fallback: boolean;
  cached: boolean;
  stale: boolean;
  updatedAt: Date;
}

export interface ApiSuccess<T> {
  success: true;
  data: T;
  meta: ResponseMeta;
}

export interface ApiFailure {
  success: false;
  error: {
    code: string;
    message: string;
    detail?: unknown;
  };
  meta: ResponseMeta;
}
