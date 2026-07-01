// Typed HTTP client for the calibration-service API and the token server.
// URLs come from Vite env (never hardcoded), see env.d.ts.

import type { ConfigRequest, DetectedCamera, Session, SessionSummary } from '@/transport/types';

export interface TokenResponse {
  token: string;
  room: string;
  identity: string;
}

const API_URL = import.meta.env.VITE_API_URL;

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`);
  if (!response.ok) {
    throw new Error(`GET ${path} failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(`POST ${path} failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export const fetchSession = (): Promise<Session> => getJson<Session>('/session');

export const fetchSessions = (): Promise<SessionSummary[]> =>
  getJson<SessionSummary[]>('/sessions');

export const detectCameras = (): Promise<DetectedCamera[]> =>
  postJson<DetectedCamera[]>('/cameras/detect');

export const configureCameras = (request: ConfigRequest): Promise<Session> =>
  postJson<Session>('/cameras/config', request);

export async function fetchToken(): Promise<TokenResponse> {
  const response = await fetch(import.meta.env.VITE_TOKEN_URL);
  if (!response.ok) {
    throw new Error(`token request failed: ${response.status}`);
  }
  return (await response.json()) as TokenResponse;
}
