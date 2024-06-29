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
from freqtrade.persistence import Trade
from typing import Optional, Tuple, Union
from freqtrade.strategy import stoploss_from_open

logger = logging.getLogger(__name__)



class WTDMIPRICESDCAtrategy(IStrategy):

    INTERFACE_VERSION: int = 3
    can_short = False
    position_adjustment_enable = True
    max_entry_position_adjustment = -1

    minimal_roi = {
        "0": 0.01
    }
    
    stoploss =  -1
    
    # trailing_stop = True
    # trailing_stop_positive = 0.01
    # trailing_stop_positive_offset = 0.02
    # trailing_only_offset_is_reached = True
    

    order_types = {
        'entry': 'market',
        'exit': 'market',
        'stoploss': 'market',
        'stoploss_on_exchange': False
    }

    # Optional order time in force.
    order_time_in_force = {
        'entry': 'GTC',
        'exit': 'GTC'
    }

    
    adxWindow = IntParameter(7, 21, default=14, space="buy")
    adxThr = IntParameter(15, 35, default=25, space="buy")
    emaThr = IntParameter(5, 55, default=24, space="buy")
    osLevel = IntParameter(-50, -60, default=-53, space="buy")
    

    # Optimal timeframe for the strategy
    timeframe = '1m'
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
        informative['ema'] = ta.EMA(informative, timeperiod=self.emaThr.value)
        
        
        n1 = 10
        n2 = 21
        ap = (dataframe['high'] + dataframe['low'] + dataframe['close']) / 3
        esa = ap.ewm(span=n1, min_periods=n1).mean()
        d = ap.sub(esa).abs().ewm(span=n1, min_periods=n1).mean()
        ci = (ap - esa) / (0.015 * d)
        tci = ci.ewm(span=n2, min_periods=n2).mean()
        dataframe['wt1'] = tci
        dataframe['wt2'] = dataframe['wt1'].rolling(window=4).mean()

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
                (dataframe['close'] < dataframe[f'ema_{self.inf_tf}'])
                & 
                (dataframe[f'plus_di_{self.inf_tf}'] > dataframe[f'minus_di_{self.inf_tf}']) & (dataframe[f'plus_di_{self.inf_tf}']>self.adxThr.value)
            ),
            'enter_long'] = 1
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

        return dataframe

    
    # DCA the initial order (opening trade)
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        return 10
    
    # DCA left order (append trade)
    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: Optional[float], max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs
                              ) -> Union[Optional[float], Tuple[Optional[float], Optional[str]]]:
        
        filled_entries = trade.select_filled_orders(trade.entry_side)
        if current_time - timedelta(minutes=1) < filled_entries[-1].order_date_utc:
            return None
                
        if current_profit > -0.01:
            return None
        
        
        # Obtain pair dataframe (just to show how to access it)
        dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        # Only buy when not actively falling price.
        prev_candle = dataframe.iloc[-2].squeeze()
        last_candle = dataframe.iloc[-1].squeeze()
        
        
        if (last_candle['wt1'] < self.osLevel.value) and (last_candle['wt1'] > last_candle['wt2']) and (prev_candle['wt1'] < prev_candle['wt2']):
            count_of_entries = trade.nr_of_successful_entries
            try:
                # This returns first order stake size
                stake_amount = filled_entries[0].stake_amount
                # This then calculates current safety order size
                stake_amount = stake_amount * (1 + (count_of_entries * 0.2))
                return stake_amount
            except Exception as exception:
                return None
        return None
    
   