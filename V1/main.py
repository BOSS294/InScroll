from app import app, CONFIG_PATH, DEFAULT_CONFIG, save_config

if __name__ == '__main__':
    if not CONFIG_PATH.exists() or CONFIG_PATH.stat().st_size == 0:
        save_config(DEFAULT_CONFIG)
    app.run(host='127.0.0.1', port=7860, debug=True)
