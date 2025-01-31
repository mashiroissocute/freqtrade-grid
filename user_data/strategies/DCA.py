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



class DCADMIPRICEStrategySpot(IStrategy):

    INTERFACE_VERSION: int = 3
    can_short = False
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    amend_last_stake_amount = True

    minimal_roi = {
        "0": 1
    }
    
    stoploss =  -0.99
    
    # Trailing stop:
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.05
    trailing_only_offset_is_reached = True

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

    adxWindow = IntParameter(7, 21, default=24, space="buy")
    adxThr = IntParameter(15, 35, default=25, space="buy")
    emaThrLong = IntParameter(5, 55, default=24, space="buy")
    emaThrShort = IntParameter(5, 55, default=24, space="buy")
    
    initStakeAmount = 10
    stakeAmountPeriod = 10
    
    smallGridPercent = 0.02
    bigGridPercent = 0.1


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
                # (dataframe['close'] < dataframe[f'emaLong_{self.inf_tf}'])
                # & 
                # (dataframe[f'plus_di_{self.inf_tf}'] > dataframe[f'minus_di_{self.inf_tf}']) & (dataframe[f'plus_di_{self.inf_tf}']>self.adxThr.value)
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
        dataframe.loc[
            (
            ),
            'exit_short'] = 0


        return dataframe
    
    
    # the initial order (opening trade)
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        
        return self.initStakeAmount
    
    
    def order_filled(self, pair: str, trade: Trade, order: Order, current_time: datetime, **kwargs) -> None:        
        
        increase_tag = 'Increase Postion'
        decrease_tag = 'Decrease Postion'
        stoploss_tag = 'Stoploss Postion'
        
        filled_entries = trade.select_filled_orders()
        first_order = filled_entries[0]
        if first_order.order_id != order.order_id: # Not trade's first order 
            return None
        
        
        
        # trade's first order
        metadataMap = {}
        metadataMap['stakeAmountList'] = []
        metadataMap['smallGrid'] = 0.0
        metadataMap['bigGrid'] = 0.0
        metadataMap['lineList'] = []
        metadataMap['fistOrderPrice'] = 0.0
        metadataMap['vaildOrderIDs'] = []
        
        current_rate = first_order.safe_price
        metadataMap['fistOrderPrice'] = current_rate
       
        # init stakeAmount
        metadataMap['stakeAmountList'] = [self.initStakeAmount, self.initStakeAmount+1*self.stakeAmountPeriod, self.initStakeAmount+2*self.stakeAmountPeriod]


        # init small price grid
        metadataMap['smallGrid'] = current_rate*self.smallGridPercent

        
        # init big price grid
        metadataMap['bigGrid'] = current_rate*self.bigGridPercent

        
        # init line        
        if trade.entry_side == "buy" :
            metadataMap['lineList'] = [current_rate, current_rate-1*metadataMap['bigGrid'],current_rate-2*metadataMap['bigGrid'],current_rate-3*metadataMap['bigGrid']]
        elif trade.entry_side == "sell" :
            metadataMap['lineList'] = [current_rate, current_rate+1*metadataMap['bigGrid'],current_rate+2*metadataMap['bigGrid'],current_rate+3*metadataMap['bigGrid']]

        
        # init orderIDS
        metadataMap['vaildOrderIDs'] = [order.order_id]

        logger.info(f"init metadata, metadataMap {metadataMap}")
        
        trade.set_custom_data(key='GRIDMETADATAS',value=metadataMap)
        return None

    
    # GRID ORDERs
    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: Optional[float], max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs
                              ) -> Union[Optional[float], Tuple[Optional[float], Optional[str]]]:
    # --------------- read and check metadata -----------------
        metadataMap = trade.get_custom_data(key='GRIDMETADATAS')
        
        
        lineList = metadataMap['lineList']
        if len(lineList) != 4 :
            logger.error(f"{trade.pair} lineList is not 4, lineMap: {lineList}")
            return None
        
        smallGrid = metadataMap['smallGrid']
        if smallGrid == 0.0 :
            logger.error(f"{trade.pair} smallGrid is 0, smallGrid: {smallGrid}")
            return None
        
        bigGrid = metadataMap['bigGrid']
        if bigGrid == 0.0 :
            logger.error(f"{trade.pair} bigGrid is 0, bigGrid: {bigGrid}")
            return None
        
        stakeAmountList = metadataMap['stakeAmountList']
        if len(stakeAmountList) != 3 :
            logger.error(f"{trade.pair} stakeAmountList is not 3, stakeAmountList: {stakeAmountList}")
            return None
        
        vaildOrderIDs = metadataMap['vaildOrderIDs']
        if len(vaildOrderIDs) == 0 :
            logger.error(f"{trade.pair} vaildOrderIDs is 0, vaildOrderIDs: {vaildOrderIDs}")
            return None
        
    # ---------------- GRID stoploss position --------------
        stoplossStartLine = lineList[0] # stoploss start line
        stoplossEndLine = lineList[1] # stoploss end line
        stoplossTriggerLine = lineList[3] # stoploss trigger line
        stoplossNextGridStackAmount = stakeAmountList[1] # stoploss next grid stack amount
        
                
    # ---------------- GRID increse position ---------------   
        line0 = lineList[0] 
        line1 = lineList[1]
        line2 = lineList[2]
        line3 = lineList[3]
        stakeAmount1 = stakeAmountList[0] # between line0 - line1
        stakeAmount2 = stakeAmountList[1] # between line1 - line2
        stakeAmount3 = stakeAmountList[2] # between line2 - line3
        
        lastOperateOrder = find_last_operate_order(trade)
        if lastOperateOrder == None:
            logger.error(f"{trade.pair} lastOperateOrder is None")
            return None
        
        # long trade increase postion where curPrice <= lastPrice - GridPrice
        if trade.entry_side == 'buy' : 
            if current_rate <= lastOperateOrder.safe_price - smallGrid:
                try:
                    priod = (line0 - current_rate) / bigGrid 
                    stake_amount = stakeAmount1 + self.stakeAmountPeriod * int(priod)
                    return stake_amount, f'Increase Postion, stake_amount: {stake_amount}, lastorderprice {lastOperateOrder.safe_price}, currentrate: {current_rate}, lines: {lineList}'
                except Exception as exception:
                    return None
                
                
        return None



def find_last_operate_order(trade: Trade) -> Order:
    stop_loss_tag = 'Stoploss Postion'
        
    
    filled_entries = trade.select_filled_orders()
    
    for order in reversed(filled_entries):
        if stop_loss_tag not in order.ft_order_tag: # exclude stoploss order
            return order
                
    return None

def find_last_valid_order(trade: Trade, orderID: str) -> Order:   
    filled_entries = trade.select_filled_orders()
    
    for order in reversed(filled_entries):
        if orderID == order.order_id: # exclude stoploss order
            return order
                
    return None



def find_valid_buyorders_betweenline(trade: Trade, vaildOrderIDs: List[str], lineUp: float, lineDown: float) -> List[Order]:
    filled_entries = trade.select_filled_orders()
    vaild_orders: List[Order] = []
    
    for order in filled_entries:
        if order.order_id in vaildOrderIDs and lineUp >= order.safe_price and order.safe_price > lineDown:
            vaild_orders.append(order)
    
    return vaild_orders



def find_valid_sellorders_betweenline(trade: Trade, vaildOrderIDs: List[str], lineDown: float, lineUp: float) -> List[Order]:
    filled_entries = trade.select_filled_orders()
    vaild_orders: List[Order] = []
    
    for order in filled_entries:
        if order.order_id in vaildOrderIDs and lineUp > order.safe_price and order.safe_price >= lineDown:
            vaild_orders.append(order)
    
    return vaild_orders


def removeStoplossOrderIDs(metaDataOrderIDs: List[str],toRemoveOrderIDs: List[str]) -> List[str]:
    
    valid_order_ids: List[str]= []
    
    for metaOrderID in metaDataOrderIDs:
        if metaOrderID not in toRemoveOrderIDs:
            valid_order_ids.append(metaOrderID)
            
    return valid_order_ids