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



class GRIDDMIPRICEStrategyFutureV2Long(IStrategy):

    INTERFACE_VERSION: int = 3
    can_short = True
    position_adjustment_enable = True
    max_entry_position_adjustment = 3
    amend_last_stake_amount = True

    minimal_roi = {
        "0": 2
    }
    
    stoploss =  -0.8
    
    trailing_stop = True
    trailing_stop_positive = 0.1
    trailing_stop_positive_offset = 0.3
    trailing_only_offset_is_reached = True
    

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

    upGridPercent = 1.09
    downGridPercent = 0.89

    

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
        return 5.0
    
    
    # DCA ORDERs
    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: Optional[float], max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs
                              ) -> Union[Optional[float], Tuple[Optional[float], Optional[str]]]:

        dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        # Only buy when not actively falling price.
        last_candle = dataframe.iloc[-1].squeeze()
        previous_candle = dataframe.iloc[-2].squeeze()
        if last_candle['close'] < previous_candle['close']:
            return None


        filled_entries = trade.select_filled_orders() # all filled entry
        last_order_price = filled_entries[-1].safe_price


        # long trade increase postion where curPrice < lastPrice*0.89
        if trade.entry_side == 'buy' : 
            if current_rate <= last_order_price * self.downGridPercent:
                stake_amount = trade.stake_amount * 2
                return stake_amount, f'Increase Postion, last order price {last_order_price}'

                
                
        # short trade increase postion where curPrice > lastPrice*1.09
        if trade.entry_side == 'sell' : 
            if current_rate >= last_order_price * self.upGridPercent:
                stake_amount = trade.stake_amount * 2
                return stake_amount, f'Increase Postion, last order price {last_order_price}'

        
        # 5 + 10 + 30 + 90
        # 0%  10%  20%  30%

        return None