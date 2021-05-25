module.exports = {
  transpileDependencies: ["vuetify"],
  devServer: {
    host: "0.0.0.0",
    port: 8080,
    disableHostCheck: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000/"
      }
    },
    historyApiFallback: {
      index: 'index.html'
    }
  },
};
