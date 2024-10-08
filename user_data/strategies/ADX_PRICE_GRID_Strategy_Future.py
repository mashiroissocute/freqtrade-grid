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
# --------------------------------

import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.persistence import Trade, Order
from typing import Optional, Tuple, Union
from freqtrade.strategy import stoploss_from_open

logger = logging.getLogger(__name__)



class GRIDDMIPRICEStrategyFuture(IStrategy):

    INTERFACE_VERSION: int = 3
    can_short = True
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    amend_last_stake_amount = True

    minimal_roi = {
        "0": 1
    }
    
    stoploss =  -1
    
    # trailing_stop = True
    # trailing_stop_positive = 0.05
    # trailing_stop_positive_offset = 0.25
    # trailing_only_offset_is_reached = True
    

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

    
    adxWindow = IntParameter(7, 21, default=14, space="buy")
    adxThr = IntParameter(15, 35, default=25, space="buy")
    emaThrLong = IntParameter(5, 55, default=12, space="buy")
    emaThrShort = IntParameter(5, 55, default=24, space="buy")
    upGridPercent = 1.02
    downGridPercent = 0.98
    upStoplossPercent = 1.25
    downStoplossPercent = 0.75
    

    # Optimal timeframe for the strategy
    timeframe = '5m'
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
            'enter_short'] = 1
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
        return 3
    
    # DCA the initial order (opening trade)
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        return 10.0
    
    
    # GRID ORDERs
    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: Optional[float], max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs
                              ) -> Union[Optional[float], Tuple[Optional[float], Optional[str]]]:
        
        vaild_order_list = find_valid_order(trade)
        if len(vaild_order_list) == 0:
            return None
        
        last_unstoploss_order = vaild_order_list[0]
        last_unconsumed_order = vaild_order_list[-1]
        
        
        last_grid_order = find_last_orders(trade)
        if last_grid_order == None:
            return None
        
        filled_entries = trade.select_filled_orders() # all filled entry
        
        # ---------------- GRID increse position ---------------
        last_order_price = last_grid_order.safe_price
        
        # long trade increase postion where curPrice < lastPrice*0.98
        if trade.entry_side == 'buy' : 
            if current_rate <= last_order_price * self.downGridPercent:
                try:
                    stake_amount = filled_entries[0].stake_amount
                    return stake_amount, f'Increase Postion, last order price {last_order_price}'
                except Exception as exception:
                    return None
                
                
        # short trade increase postion where curPrice > lastPrice*1.02
        if trade.entry_side == 'sell' : 
            if current_rate >= last_order_price * self.upGridPercent:
                try:
                    # This returns first order stake size
                    stake_amount = filled_entries[0].stake_amount
                    return stake_amount, f'Increase Postion, last order price {last_order_price}'
                except Exception as exception:
                    return None
    
        
        # ---------------- GRID decrease position --------------
        last_oppsite_order = last_unconsumed_order

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
                
                
        # ---------------- GRID stoploss position --------------
        last_stoploss_order = last_unstoploss_order

        # long trade decrese postion where curPrice > lastPrice*1.02
        if trade.entry_side == 'buy' : 
            if current_rate <= last_stoploss_order.safe_price / self.downStoplossPercent:
                try:
                    # This returns oppsite order amount size
                    # stake_amount = last_oppsite_order.safe_amount * current_exit_rate / trade.leverage
                    return -last_stoploss_order.safe_amount, f'Stoploss Postion, oppsite order price: {last_oppsite_order.safe_price} amount: {last_oppsite_order.safe_amount}'
                except Exception as exception:
                    return None
                
                
        # short trade decrease postion where curPrice < lastPrice*0.98
        if trade.entry_side == 'sell' : 
            if current_rate >= last_stoploss_order.safe_price  / self.upStoplossPercent:
                try:
                    # This returns oppsite order amount size
                    # stake_amount = last_oppsite_order.safe_amount * current_exit_rate / trade.leverage
                    return -last_stoploss_order.safe_amount, f'Stoploss Postion, oppsite order price: {last_oppsite_order.safe_price} amount: {last_oppsite_order.safe_amount}'
                except Exception as exception:
                    return None
                    
        
        return None
    
    
    
def find_valid_order(trade: Trade) -> List[Order]:
    decrease_tag = 'Decrese Postion'
    stop_loss_tag = 'Stoploss Postion'
    
    buy_stack: List[Order] = []
    
    vaild_order: List[Order] = []
    
    
    filled_entries = trade.select_filled_orders()
    init_order_side = trade.entry_side
    
    for order in filled_entries:
    # logger.info(f'pair:{order.ft_pair}, orderid:{order.order_id},orderside:{order.ft_order_side}')
        if order.ft_order_side == init_order_side:
            # logger.info(f'stake append orderid:{order.order_id}')
            buy_stack.append(order)
        else:
            # logger.info(f'stake pop orderid:{order.order_id}')
            if decrease_tag in order.ft_order_tag:
                buy_stack.pop()
            elif stop_loss_tag in order.ft_order_tag:
                buy_stack.pop(0)
                
    
    if len(buy_stack) == 0:
        return vaild_order
    
    # buy_stack[0] first unstoploss order
    # buy_stack[-1] first unconsumed order
    vaild_order.append(buy_stack[0],buy_stack[-1]) #first unstop
    
    return vaild_order
    
    
    
def find_last_orders(trade: Trade) -> Order:
    stop_loss_tag = 'Stoploss Postion'
        
    
    filled_entries = trade.select_filled_orders()
    
    for order in reversed(filled_entries):
        if stop_loss_tag not in order.ft_order_tag:
            return order
                
    return None



    