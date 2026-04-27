import logging

logger = logging.getLogger("RiskManager")

class RiskManager:
    def __init__(self, initial_balance: float, max_daily_loss_pct: float = 4.5, max_overall_loss_pct: float = 9.0):
        """
        FTMO rules generally define 5% daily and 10% overall loss. 
        We use 4.5% and 9.0% as hard internal stops to prevent any slippage from breaching FTMO rules.
        """
        self.initial_balance = initial_balance
        self.max_daily_loss_pct = max_daily_loss_pct / 100.0
        self.max_overall_loss_pct = max_overall_loss_pct / 100.0
        
    def check_daily_limit(self, current_equity: float, start_of_day_balance: float) -> bool:
        """
        Checks if the current equity is above the daily loss limit.
        Daily limit is calculated relative to the start_of_day_balance.
        """
        max_loss_amount = start_of_day_balance * self.max_daily_loss_pct
        allowed_minimum_equity = start_of_day_balance - max_loss_amount
        
        if current_equity <= allowed_minimum_equity:
            logger.error(f"DAILY LIMIT BREACH RISK! Equity: {current_equity}, Allowed Min: {allowed_minimum_equity}")
            return False
        return True

    def check_overall_limit(self, current_equity: float) -> bool:
        """
        Checks if the current equity is above the overall loss limit.
        Overall limit is calculated relative to the initial_balance of the challenge.
        """
        max_loss_amount = self.initial_balance * self.max_overall_loss_pct
        allowed_minimum_equity = self.initial_balance - max_loss_amount
        
        if current_equity <= allowed_minimum_equity:
            logger.error(f"OVERALL LIMIT BREACH RISK! Equity: {current_equity}, Allowed Min: {allowed_minimum_equity}")
            return False
        return True

    def calculate_position_size(self, current_equity: float, entry_price: float, stop_loss_price: float, risk_pct: float = 0.5) -> float:
        """
        Calculates position size based on dollar risk amount.
        Returns the quantity of the asset to buy/sell.
        """
        if entry_price == stop_loss_price:
            return 0.0

        risk_amount = current_equity * (risk_pct / 100.0)
        
        # Calculate dollar risk per 1 full unit of the asset (e.g. 1 BTC)
        price_difference = abs(entry_price - stop_loss_price)
        
        # size = Risk $ / Risk per 1 unit
        size = risk_amount / price_difference
        
        # The executor should round this to the broker's step size
        return size

    def can_open_trade(self, current_equity: float, start_of_day_balance: float, current_open_trades: int, max_trades: int = 2) -> bool:
        """
        Pre-trade check incorporating multiple FTMO safety filters.
        """
        if current_open_trades >= max_trades:
            logger.warning("Max concurrent trades reached. Cannot open new trade.")
            return False
            
        if not self.check_daily_limit(current_equity, start_of_day_balance):
            return False
            
        if not self.check_overall_limit(current_equity):
            return False
            
        return True
