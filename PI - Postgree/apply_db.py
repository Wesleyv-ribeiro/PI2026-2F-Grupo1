from app import init_db, seed_admin

if __name__ == '__main__':
    init_db()
    seed_admin()
    print('DB init and admin seed completed')
