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
import json
import os
# --------------------------------

import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.persistence import Trade, Order
from typing import Optional, Tuple, Union
from freqtrade.strategy import stoploss_from_open

logger = logging.getLogger(__name__)



class RebalanceStrategySpot(IStrategy):

    INTERFACE_VERSION: int = 3
    can_short = False
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    amend_last_stake_amount = True

    minimal_roi = {
        "0": 1
    }
    
    stoploss =  -0.1
    
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

   
    upPercent = 0.022
    fixPercent = 0.02
    downPercent = 0.018
    
   
    timeframe = '1m'    
    
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Based on TA indicators, populates the buy signal for the given dataframe
        :param dataframe: DataFrame
        :return: DataFrame with buy column
        """
        dataframe.loc[
            (
                (dataframe['close'] > 0)
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
    
    

    #  ORDERs
    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: Optional[float], max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs
                              ) -> Union[Optional[float], Tuple[Optional[float], Optional[str]]]:
        
        allStakeAmount = self.wallets.get_total('USDT')
        trades = Trade.get_trades_proxy(is_open=True)
        for singletrade in trades:            
            pairRate = self.dp._exchange.get_rate(singletrade.pair,refresh=False,side='exit',is_short=False) 
            allStakeAmount += singletrade.amount*pairRate
            logger.error(f'{singletrade.pair} stake,{singletrade.amount*pairRate}')
        currStakeAmount = current_rate * trade.amount
        # logger.error(f'allStakeAmount,{allStakeAmount}')
        # logger.error(f'currStakeAmount,{currStakeAmount}')
        
        
        
        
        if currStakeAmount / allStakeAmount > self.upPercent:
            logger.error(f'sell,{(allStakeAmount * self.fixPercent) - currStakeAmount}')
            return  (allStakeAmount * self.fixPercent) - currStakeAmount

        
        if currStakeAmount / allStakeAmount < self.downPercent:
            logger.error(f'buy,{(allStakeAmount * self.fixPercent) - currStakeAmount}')
            return  (allStakeAmount * self.fixPercent) - currStakeAmount
        
        