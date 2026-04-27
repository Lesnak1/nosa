import MetaTrader5 as mt5
import logging

logger = logging.getLogger("MT5Executor")

class MT5Executor:
    def __init__(self, account_number=None, password=None, server=None):
        self.account_number = account_number
        self.password = password
        self.server = server
        self.sod_balance = None
        
    def connect(self):
        logger.info("Connecting to MT5 (Bulletproof Mode)...")
        
        # Always shutdown before a fresh start to clear stalled IPC pipes
        mt5.shutdown()
        
        # Step 1: Attempt standard initialization (letting library find terminal)
        # We use a 120s timeout as suggested by professional documentation
        init_ok = mt5.initialize(timeout=120000)
        
        if not init_ok:
            error_code, error_desc = mt5.last_error()
            logger.warning(f"Standard initialize failed: {error_code} ({error_desc})")
            
            # Step 2: If standard fails, try explicit path (calculated or manual)
            path = r"C:\Users\philo\AppData\Roaming\MetaTrader 5\terminal64.exe"
            logger.info(f"Attempting explicit path initialization: {path}")
            init_ok = mt5.initialize(path=path, timeout=120000)
            
            if not init_ok:
                # Step 3: Try portable mode just in case (some FTMO installs use this)
                logger.warning("Path init failed. Trying Portable Mode...")
                init_ok = mt5.initialize(path=path, timeout=120000, portable=True)
                
                if not init_ok:
                    error_code, error_desc = mt5.last_error()
                    # Check if IPC is actually alive despite the error
                    if mt5.terminal_info() is not None:
                        logger.warning("IPC is secretly alive! Proceeding despite init error.")
                        init_ok = True
                    else:
                        logger.error(f"ALL INITIALIZATION ATTEMPTS FAILED. Final Error: {error_code} ({error_desc})")
                        logger.error("CRITICAL: Please ensure BOTH Python and MT5 are running with SAME PRIVILEGES (both as Admin or both as Standard).")
                        return False

        # Step 4: Login explicitly if initialization succeeded (or was bypassed)
        if init_ok:
            if self.account_number and self.password and self.server:
                acc_info = mt5.account_info()
                if acc_info and acc_info.login == self.account_number:
                    logger.info(f"Already logged in to account {self.account_number}.")
                else:
                    logger.info(f"Logging into MT5 account {self.account_number} on {self.server}...")
                    authorized = mt5.login(
                        login=self.account_number, 
                        password=self.password, 
                        server=self.server
                    )
                    if not authorized:
                        error_code, error_desc = mt5.last_error()
                        logger.error(f"Login failed at account #{self.account_number}, error code: {error_code} ({error_desc})")
                        return False
                
        account_info = mt5.account_info()
        if account_info is None:
            logger.error("Failed to get account info")
            return False
            
        logger.info(f"Connected to MT5! Account: {account_info.login}, Balance: {account_info.balance}, Equity: {account_info.equity}")
        self.sod_balance = account_info.balance # Simplification: Assume script starts at beginning of day. In prod, fetch daily open balance.
        return True

    def get_equity(self) -> float:
        acc = mt5.account_info()
        return acc.equity if acc else 0.0

    def get_balance(self) -> float:
        acc = mt5.account_info()
        return acc.balance if acc else 0.0
        
    def get_start_of_day_balance(self) -> float:
        return self.sod_balance if self.sod_balance else self.get_balance()

    def get_open_positions_count(self) -> int:
        positions = mt5.positions_get()
        if positions is None:
            return 0
        return len(positions)

    def close_all_positions(self):
        positions = mt5.positions_get()
        if positions is None or len(positions) == 0:
            return
            
        logger.warning(f"Closing ALL {len(positions)} positions immediately!")
        for pos in positions:
            tick = mt5.symbol_info_tick(pos.symbol)
            type_dict = {
                mt5.POSITION_TYPE_BUY: mt5.ORDER_TYPE_SELL,
                mt5.POSITION_TYPE_SELL: mt5.ORDER_TYPE_BUY
            }
            price_dict = {
                mt5.ORDER_TYPE_SELL: tick.bid,
                mt5.ORDER_TYPE_BUY: tick.ask
            }
            
            close_type = type_dict[pos.type]
            close_price = price_dict[close_type]
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "position": pos.ticket,
                "price": close_price,
                "deviation": 20,
                "magic": 123456,
                "comment": "Emergency Close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error(f"Failed to close position {pos.ticket}: {result.comment}")

    def open_trade(self, symbol: str, side: str, volume: float, sl: float, tp: float) -> bool:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logger.error(f"{symbol} not found in MT5")
            return False
            
        if not symbol_info.visible:
            if not mt5.symbol_select(symbol, True):
                logger.error(f"symbol_select({symbol}) failed")
                return False
                
        # Normalize volume to symbol steps
        volume = float(round(volume / symbol_info.volume_step) * symbol_info.volume_step)
        if volume < symbol_info.volume_min:
            volume = symbol_info.volume_min
            
        tick = mt5.symbol_info_tick(symbol)
        
        order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if side == "BUY" else tick.bid
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 10,
            "magic": 123456,
            "comment": "FTMO Bot Entry",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order send failed, retcode={result.retcode} ({result.comment})")
            return False
            
        return True
        
    def shutdown(self):
        mt5.shutdown()
