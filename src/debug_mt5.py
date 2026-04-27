import MetaTrader5 as mt5
import os

path = r"C:\Users\philo\AppData\Roaming\MetaTrader 5\terminal64.exe"
login = 1513218181
password = "m$*876AJ*6v!5c"
server = "FTMO-Demo"

print(f"--- MT5 Debug Script ---")
print(f"Path exists: {os.path.exists(path)}")

mt5.shutdown()
print("Attempting initialize with portable=False...")
ok = mt5.initialize(
    path=path,
    login=login,
    password=password,
    server=server,
    timeout=120000,
    portable=False
)

if not ok:
    print(f"Failed (False). Error: {mt5.last_error()}")
    print("Attempting initialize with portable=True...")
    ok = mt5.initialize(
        path=path,
        login=login,
        password=password,
        server=server,
        timeout=120000,
        portable=True
    )

if ok:
    print("SUCCESS!")
    print("Terminal Info:", mt5.terminal_info())
    print("Account Info:", mt5.account_info())
    mt5.shutdown()
else:
    print(f"STILL FAILED. Last error: {mt5.last_error()}")
