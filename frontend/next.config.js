/** @type {import('next').NextConfig} */
const nextConfig = {
  // Apryse WebViewer static assets will be copied to public/webviewer/lib
  // via a postinstall script or manual copy from node_modules/@pdftron/webviewer/public
  webpack: (config) => {
    config.resolve.alias.canvas = false;
    return config;
  },
};

module.exports = nextConfig;
