from app import create_app, init_db, seed_admin

if __name__ == '__main__':
    init_db()
    seed_admin()
    app = create_app()
    print('Starting Flask')
    app.run(debug=True)
