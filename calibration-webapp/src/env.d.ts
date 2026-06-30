/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_HOST_IP: string;
  readonly VITE_API_URL: string;
  readonly VITE_LIVEKIT_URL: string;
  readonly VITE_TOKEN_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
