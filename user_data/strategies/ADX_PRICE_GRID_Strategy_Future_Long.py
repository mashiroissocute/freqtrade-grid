# --- Do not remove these libs ---
from freqtrade.strategy import IStrategy,merge_informative_pair
from typing import Dict, List
from functools import reduce
from pandas import DataFrame
from datetime import datetime, timedelta, timezone
from typing import Optional
from freqtrade.strategy import CategoricalParameter, DecimalParameter, IntParameter
from freqtrade.persistence import PairLocks
import logging
import math
# --------------------------------

import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.persistence import Trade, Order
from typing import Optional, Tuple, Union
from freqtrade.strategy import stoploss_from_open

logger = logging.getLogger(__name__)



class GRIDDMIPRICEStrategyFutureV3Long(IStrategy):

    INTERFACE_VERSION: int = 3
    can_short = False
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    amend_last_stake_amount = True

    minimal_roi = {
        "0": 100
    }
    
    stoploss =  -2
    
    order_types = {
        'entry': 'market',
        'exit': 'market',
        'stoploss': 'market',
        'stoploss_on_exchange': True
    }

    # Optional order time in force.
    order_time_in_force = {
        'entry': 'GTC',
        'exit': 'GTC'
    }

    
    adxWindow = IntParameter(7, 42, default=24, space="buy") 
    adxThr = IntParameter(15, 35, default=25, space="buy") 
    emaThrLong = IntParameter(5, 55, default=24, space="buy")
    emaThrShort = IntParameter(5, 55, default=24, space="buy")
    upGridPercent = 1.02
    downGridPercent = 0.98
    
    upGridLimit1 = 1.1    
    upGridLimit2 = 1.2
    upGridLimit3 = 1.3
    upGridLimit4 = 1.4
    upGridLimit5 = 1.5

    downGridLimit1 = 0.9
    downGridLimit2 = 0.8
    downGridLimit3 = 0.7
    downGridLimit4 = 0.6
    downGridLimit5 = 0.5

    
    GridAmount1 = 8  #  0 - 10%   8 * 5 = 40u
    GridAmount2 = 10 # 10 - 20%  10 * 6 = 60u
    GridAmount3 = 12 # 20 - 30%  12 * 6 = 72u
    GridAmount4 = 14 # 30 - 40%  14 * 8 = 112u
    GridAmount5 = 16 # 40 - 50%  16 * 9 = 144u
    # total 428
    
    # Optimal timeframe for the strategy
    timeframe = '4h'
    inf_tf = '4h'    
        
    def informative_pairs(self):
        # get access to all pairs available in whitelist.
        pairs = self.dp.current_whitelist()
        # Assign tf to each pair so they can be downloaded and cached for strategy.
        informative_pairs = [(pair, self.inf_tf) for pair in pairs]
        return informative_pairs
    

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        adxWindow = self.adxWindow.value
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe=self.inf_tf)
        informative['plus_di'] = ta.PLUS_DI(informative,adxWindow)
        informative['minus_di'] = ta.MINUS_DI(informative,adxWindow)
        informative['emaLong'] = ta.EMA(informative, timeperiod=self.emaThrLong.value)
        informative['emaShort'] = ta.EMA(informative, timeperiod=self.emaThrShort.value)
        
        
        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, self.inf_tf, ffill=True)    

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Based on TA indicators, populates the buy signal for the given dataframe
        :param dataframe: DataFrame
        :return: DataFrame with buy column
        """
        dataframe.loc[
            (
                (dataframe['close'] < dataframe[f'emaLong_{self.inf_tf}'])
                & 
                (dataframe[f'plus_di_{self.inf_tf}'] > dataframe[f'minus_di_{self.inf_tf}']) & (dataframe[f'plus_di_{self.inf_tf}']>self.adxThr.value)
            ),
            'enter_long'] = 1
        
        dataframe.loc[
            (
                (dataframe['close'] > dataframe[f'emaShort_{self.inf_tf}'])
                & 
                (dataframe[f'plus_di_{self.inf_tf}'] < dataframe[f'minus_di_{self.inf_tf}']) & (dataframe[f'minus_di_{self.inf_tf}']>self.adxThr.value)
            ),
            'enter_short'] = 0
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Based on TA indicators, populates the sell signal for the given dataframe
        :param dataframe: DataFrame
        :return: DataFrame with buy column
        """
        dataframe.loc[
            (
            ),
            'exit_long'] = 0
        dataframe.loc[
            (
            ),
            'exit_short'] = 0


        return dataframe
    
    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        return 2.0
    
    # DCA the initial order (opening trade)
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        return self.GridAmount1
    
    
    # GRID ORDERs
    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: Optional[float], max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs
                              ) -> Union[Optional[float], Tuple[Optional[float], Optional[str]]]:
                
        # ---------------- GRID increse position ---------------
        filled_entries = trade.select_filled_orders() # all filled entry
        last_order_price = filled_entries[-1].safe_price
        first_order_price = filled_entries[0].safe_price
        
        # long trade increase postion where curPrice < lastPrice*0.98
        if trade.entry_side == 'buy' : 
            if current_rate <= last_order_price * self.downGridPercent:
                try:
                    if first_order_price*self.downGridLimit1<=current_rate and current_rate < first_order_price:
                        stake_amount = self.GridAmount1
                    elif  first_order_price*self.downGridLimit2<=current_rate and current_rate < first_order_price*self.downGridLimit1:
                        stake_amount = self.GridAmount2
                    elif  first_order_price*self.downGridLimit3<=current_rate and current_rate < first_order_price*self.downGridLimit2:
                        stake_amount = self.GridAmount3
                    elif  first_order_price*self.downGridLimit4<=current_rate and current_rate < first_order_price*self.downGridLimit3:
                        stake_amount = self.GridAmount4
                    elif  first_order_price*self.downGridLimit5<=current_rate and current_rate < first_order_price*self.downGridLimit4:
                        stake_amount = self.GridAmount5
                    return stake_amount, f'Increase Postion, last order price {last_order_price}'
                except Exception as exception:
                    return None
                
                
        # short trade increase postion where curPrice > lastPrice*1.02
        if trade.entry_side == 'sell' : 
            if current_rate >= last_order_price * self.upGridPercent:
                try:
                    if first_order_price*self.upGridLimit1>=current_rate and current_rate > first_order_price:
                        stake_amount = self.GridAmount1
                    elif  first_order_price*self.upGridLimit2>=current_rate and current_rate > first_order_price*self.upGridLimit1:
                        stake_amount = self.GridAmount2
                    elif  first_order_price*self.upGridLimit3>=current_rate and current_rate > first_order_price*self.upGridLimit2:
                        stake_amount = self.GridAmount3
                    elif  first_order_price*self.upGridLimit4>=current_rate and current_rate > first_order_price*self.upGridLimit3:
                        stake_amount = self.GridAmount4
                    elif  first_order_price*self.upGridLimit5>=current_rate and current_rate > first_order_price*self.upGridLimit4:
                        stake_amount = self.GridAmount5
                    return stake_amount, f'Increase Postion, last order price {last_order_price}'
                except Exception as exception:
                    return None
    
        # ---------------- GRID decrease position --------------
        last_oppsite_order = find_oppsite_orders(trade)
        if last_oppsite_order is None:
            logger.error(f'pair:{trade.pair}, can not find oppsite order, please check')
            return None 
            
        # long trade decrese postion where curPrice > lastPrice*1.02
        if trade.entry_side == 'buy' : 
            if current_rate >= last_oppsite_order.safe_price / self.downGridPercent:
                try:
                    # This returns oppsite order amount size
                    # stake_amount = last_oppsite_order.safe_amount * current_exit_rate / trade.leverage
                    return -last_oppsite_order.safe_amount, f'Decrese Postion, oppsite order price: {last_oppsite_order.safe_price} amount: {last_oppsite_order.safe_amount}'
                except Exception as exception:
                    return None
                
                
        # short trade decrease postion where curPrice < lastPrice*0.98
        if trade.entry_side == 'sell' : 
            if current_rate <= last_oppsite_order.safe_price  / self.upGridPercent:
                try:
                    # This returns oppsite order amount size
                    # stake_amount = last_oppsite_order.safe_amount * current_exit_rate / trade.leverage
                    return -last_oppsite_order.safe_amount, f'Decrese Postion, oppsite order price: {last_oppsite_order.safe_price} amount: {last_oppsite_order.safe_amount}'
                except Exception as exception:
                    return None                
        
        return None
    
   
def find_oppsite_orders(trade: Trade) -> Order:
    stack = []
    filled_entries = trade.select_filled_orders()
    
    init_order_side = trade.entry_side
    # logger.info(f'init_order_side:{init_order_side}')
    
    for order in filled_entries:
        # logger.info(f'pair:{order.ft_pair}, orderid:{order.order_id},orderside:{order.ft_order_side}')
        if order.ft_order_side == init_order_side:
            # logger.info(f'stake append orderid:{order.order_id}')
            stack.append(order)
        else:
            # logger.info(f'stake pop orderid:{order.order_id}')
            stack.pop()
            
    if len(stack) == 0:
        # 一般不存在的错误情况
        return None

    return stack[-1]