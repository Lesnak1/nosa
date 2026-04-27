import logging

logger = logging.getLogger("RiskManager")

class RiskManager:
    """
    FTMO Risk Manager - Supports both 1-Step and 2-Step challenge types.
    
    FTMO 1-Step Rules (10K account):
      - Max Daily Loss: 3% of Initial Capital ($300)
      - Max Loss (Trailing, EOD): 10% of Initial Capital ($1000)
      - Profit Target: 10% ($1000)
      - Best Day Rule: No single day > 50% of total profitable days' sum
      
    FTMO 2-Step Rules (10K account):
      - Max Daily Loss: 5% of Initial Capital ($500)
      - Max Loss (Static): 10% of Initial Capital ($1000)
      - Profit Target Phase 1: 10%, Phase 2: 5%
      - Minimum Trading Days: 4

    We use internal safety margins (0.5% buffer) to prevent slippage from
    breaching the actual FTMO limits.
    """
    def __init__(self, initial_balance: float, challenge_type: str = "2-step"):
        """
        Args:
            initial_balance: Starting balance of the FTMO account.
            challenge_type: "1-step" or "2-step"
        """
        self.initial_balance = initial_balance
        self.challenge_type = challenge_type.lower()
        
        if self.challenge_type == "1-step":
            # 1-Step: 3% daily loss, 10% trailing (EOD) max loss
            self.max_daily_loss_pct = 0.025  # 2.5% internal (FTMO limit 3%)
            self.max_overall_loss_pct = 0.09  # 9% internal (FTMO trailing 10%)
            self.profit_target_pct = 0.10     # 10%
        else:
            # 2-Step: 5% daily loss, 10% static max loss
            self.max_daily_loss_pct = 0.045   # 4.5% internal (FTMO limit 5%)
            self.max_overall_loss_pct = 0.09  # 9% internal (FTMO limit 10%)
            self.profit_target_pct_p1 = 0.10  # Phase 1: 10%
            self.profit_target_pct_p2 = 0.05  # Phase 2: 5%
        
        # Tracking for Best Day Rule (1-Step)
        self.daily_pnl = {}  # date -> pnl
        
        logger.info(f"RiskManager initialized: {self.challenge_type} | "
                     f"Initial Balance: ${initial_balance:,.2f} | "
                     f"Daily Limit: {self.max_daily_loss_pct*100:.1f}% | "
                     f"Overall Limit: {self.max_overall_loss_pct*100:.1f}%")
        
    def check_daily_limit(self, current_equity: float, start_of_day_balance: float) -> bool:
        """
        FTMO Daily Loss: equity cannot drop below (SOD_balance - max_daily_loss_amount).
        The max_daily_loss_amount is a percentage of INITIAL capital, not current balance.
        """
        max_loss_amount = self.initial_balance * self.max_daily_loss_pct
        allowed_minimum_equity = start_of_day_balance - max_loss_amount
        
        if current_equity <= allowed_minimum_equity:
            logger.error(f"DAILY LIMIT BREACH RISK! Equity: {current_equity:.2f}, "
                        f"Allowed Min: {allowed_minimum_equity:.2f} "
                        f"(SOD: {start_of_day_balance:.2f} - ${max_loss_amount:.2f})")
            return False
        
        # Warning at 70% of daily limit usage
        used = start_of_day_balance - current_equity
        if used > max_loss_amount * 0.7:
            logger.warning(f"Daily limit usage at {used/max_loss_amount*100:.0f}% "
                          f"(${used:.2f} of ${max_loss_amount:.2f})")
        return True

    def check_overall_limit(self, current_equity: float) -> bool:
        """
        FTMO Overall Loss: equity cannot drop below (initial_balance - max_overall_loss_amount).
        For 2-Step this is static. For 1-Step this is trailing (EOD) but we use
        static as a conservative approximation.
        """
        max_loss_amount = self.initial_balance * self.max_overall_loss_pct
        allowed_minimum_equity = self.initial_balance - max_loss_amount
        
        if current_equity <= allowed_minimum_equity:
            logger.error(f"OVERALL LIMIT BREACH RISK! Equity: {current_equity:.2f}, "
                        f"Allowed Min: {allowed_minimum_equity:.2f}")
            return False
        return True

    def calculate_position_size(self, current_equity: float, entry_price: float, 
                                 stop_loss_price: float, risk_pct: float = 0.5) -> float:
        """
        Calculates position size based on dollar risk amount.
        Risk is capped so a single SL hit never exceeds 50% of daily loss limit.
        """
        if entry_price == stop_loss_price:
            return 0.0

        risk_amount = current_equity * (risk_pct / 100.0)
        
        # FTMO safety: cap single trade risk at 50% of daily loss limit
        daily_limit_amount = self.initial_balance * self.max_daily_loss_pct
        max_single_risk = daily_limit_amount * 0.5
        if risk_amount > max_single_risk:
            risk_amount = max_single_risk
            logger.warning(f"Risk capped to ${risk_amount:.2f} (50% of daily limit)")
        
        price_difference = abs(entry_price - stop_loss_price)
        size = risk_amount / price_difference
        
        return size

    def can_open_trade(self, current_equity: float, start_of_day_balance: float, 
                       current_open_trades: int, max_trades: int = 1) -> bool:
        """
        Pre-trade check incorporating multiple FTMO safety filters.
        We allow max 1 concurrent trade for safety (conservative approach).
        """
        if current_open_trades >= max_trades:
            logger.warning("Max concurrent trades reached. Cannot open new trade.")
            return False
            
        if not self.check_daily_limit(current_equity, start_of_day_balance):
            return False
            
        if not self.check_overall_limit(current_equity):
            return False
        
        # Extra safety: don't trade if remaining daily budget is too thin
        daily_limit_amount = self.initial_balance * self.max_daily_loss_pct
        used_today = max(0, start_of_day_balance - current_equity)
        remaining = daily_limit_amount - used_today
        if remaining < daily_limit_amount * 0.3:
            logger.warning(f"Only ${remaining:.2f} of daily limit remaining. Stopping for today.")
            return False
            
        return True

    def check_profit_target(self, current_balance: float, phase: int = 1) -> bool:
        """Check if profit target has been reached."""
        if self.challenge_type == "1-step":
            target = self.initial_balance * (1 + self.profit_target_pct)
        else:
            pct = self.profit_target_pct_p1 if phase == 1 else self.profit_target_pct_p2
            target = self.initial_balance * (1 + pct)
        
        if current_balance >= target:
            logger.info(f"🎯 PROFIT TARGET REACHED! Balance: ${current_balance:.2f} >= ${target:.2f}")
            return True
        return False
