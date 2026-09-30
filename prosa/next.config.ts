import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // As chaves de API só existem no servidor. Nada aqui é exposto ao browser.
  poweredByHeader: false,
};

export default nextConfig;
