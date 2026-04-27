import MetaTrader5 as mt5
import os
import time

path = r"C:\Users\philo\AppData\Roaming\MetaTrader 5\terminal64.exe"
login = 1513218181
password = "m$*876AJ*6v!5c"
server = "FTMO-Demo"

print(f"Launching terminal manually via os.startfile...")
os.startfile(path)
print("Waiting 15 seconds for terminal to stabilize...")
time.sleep(15)

print("Initializing mt5 (standard)...")
ok = mt5.initialize()
if not ok:
    print(f"Failed standard init. Error: {mt5.last_error()}")
    print("Trying login anyway...")
    
authorized = mt5.login(login=login, password=password, server=server)
if authorized:
    print("LOGIN SUCCESSFUL!")
    print(mt5.account_info())
else:
    print(f"LOGIN FAILED. Error: {mt5.last_error()}")

mt5.shutdown()
