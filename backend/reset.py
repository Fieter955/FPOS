import sqlite3
import bcrypt

def reset_semua_password():
    print("Membuka database ipos.db...")
    try:
        conn = sqlite3.connect("ipos.db")
        c = conn.cursor()
        
        # Ambil semua data user yang ada di tabel
        c.execute("SELECT id, username FROM users")
        users = c.fetchall()
        
        if not users:
            print("TIDAK ADA USER SAMA SEKALI di dalam database ini!")
            return
            
        print(f"\nDitemukan {len(users)} akun di database lama Anda:")
        
        # Buat password baru: admin123
        salt = bcrypt.gensalt()
        hashed_pw = bcrypt.hashpw(b"admin123", salt).decode("utf-8")
        
        # Reset password semua user yang ditemukan
        for user in users:
            user_id = user[0]
            username = user[1]
            print(f" 👉 Username terdeteksi: {username}")
            
            c.execute("UPDATE users SET hashed_password = ? WHERE id = ?", (hashed_pw, user_id))
            
        conn.commit()
        conn.close()
        
        print("\n✅ BERHASIL! Password semua akun di atas telah direset paksa.")
        print("Gunakan salah satu username di atas dengan password: admin123")
        
    except Exception as e:
        print(f"Terjadi error: {e}")

if __name__ == "__main__":
    reset_semua_password()