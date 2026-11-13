from db_compat import connect_postgres


def _table_name() -> str:
    return "legacy_user_roles"


def _connect():
    return connect_postgres()

def update_user(email, first_name, last_name, role):
    with _connect() as conn:
        conn.execute(f'''
            UPDATE {_table_name()}
            SET first_name = ?, last_name = ?, role = ?, updated_at = CURRENT_TIMESTAMP
            WHERE email = ?
        ''', (first_name, last_name, role, email))

def delete_user(email):
    with _connect() as conn:
        conn.execute(f'DELETE FROM {_table_name()} WHERE email = ?', (email,))

def get_user_role(email):
    """Get the role of a user by email"""
    with _connect() as conn:
        result = conn.execute(f'SELECT role FROM {_table_name()} WHERE email = ?', (email,)).fetchone()
    
    if result:
        return result[0]
    return None


def init_database():
    table = _table_name()
    with _connect() as conn:
        conn.execute(f'''
        CREATE TABLE IF NOT EXISTS {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            first_name TEXT,
            last_name TEXT,
            role TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        columns = [column[1] for column in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if 'first_name' not in columns:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN first_name TEXT')
        if 'last_name' not in columns:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN last_name TEXT')
        if 'updated_at' not in columns:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP')

        conn.execute(f'''
            INSERT INTO {table} (email, first_name, last_name, role)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                role = excluded.role,
                updated_at = CURRENT_TIMESTAMP
        ''', ('019919@intranet.slt.com.lk', 'Tharusha', 'Dassanayake', 'admin'))
    
def add_user(email, role, first_name=None, last_name=None):
    with _connect() as conn:
        conn.execute(f'''
            INSERT INTO {_table_name()} (email, first_name, last_name, role)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                role = excluded.role,
                updated_at = CURRENT_TIMESTAMP
        ''', (email, first_name, last_name, role))


def get_all_users():
    with _connect() as conn:
        users = conn.execute(f'SELECT email, first_name, last_name, role FROM {_table_name()} ORDER BY email').fetchall()
    
    return [{'email': user[0], 'firstName': user[1], 'lastName': user[2], 'role': user[3]} for user in users]
