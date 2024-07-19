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



class GRIDDMIPRICEStrategyFutureV6(IStrategy):

    INTERFACE_VERSION: int = 3
    can_short = True
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    amend_last_stake_amount = True

    minimal_roi = {
        "0": 3
    }
    
    stoploss =  -3    

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

    adxWindow = IntParameter(7, 21, default=24, space="buy")
    adxThr = IntParameter(15, 35, default=25, space="buy")
    emaThrLong = IntParameter(5, 55, default=24, space="buy")
    emaThrShort = IntParameter(5, 55, default=24, space="buy")
    
    initStakeAmount = 10
    stakeAmountPeriod = 1
    
    smallGridPercent = 0.006
    bigGridPercent = 0.1
    
    windowSize = 30
    
    
    Z = 1
    LowRatio = 1
    HighRatio = 0.3
    
    
    sameStackAmountGirds = 20


    # Optimal timeframe for the strategy
    timeframe = '1m'
    inf_tf = '1d'    
    
    startup_candle_count = 30
        
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
        informative['lowestPrice'] = informative['low'].rolling(window=self.windowSize).min()
        informative['highestPrice'] = informative['high'].rolling(window=self.windowSize).max()
        

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
        
        dataframe.loc[
            (
                # (dataframe['close'] > dataframe[f'emaShort_{self.inf_tf}'])
                # & 
                # (dataframe[f'plus_di_{self.inf_tf}'] < dataframe[f'minus_di_{self.inf_tf}']) & (dataframe[f'minus_di_{self.inf_tf}']>self.adxThr.value)
                (dataframe['close'] < 0)
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
    
    # the initial order (opening trade)
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        
        
        # ------------------ caculate first order ratio ------------------
        
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1].squeeze()
        
        lowestPrice = last_candle[f'lowestPrice_{self.inf_tf}']
        highestPrice = last_candle[f'highestPrice_{self.inf_tf}']
        
        if current_rate < lowestPrice:
            lowestPrice = current_rate
        
        if current_rate > highestPrice:
            highestPrice = current_rate
              
        # y = kx + b
        
        k = (self.ratioLow - self.ratioHigh) / (lowestPrice - highestPrice)
        b = (self.rotioHigh*lowestPrice - self.ratioLow*highestPrice) / (lowestPrice - highestPrice)
           
        ratio = k*current_rate + b
        
        
        return self.initStakeAmount * ratio
    
    
    def order_filled(self, pair: str, trade: Trade, order: Order, current_time: datetime, **kwargs) -> None:        
        
        increase_tag = 'Increase Postion'
        decrease_tag = 'Decrease Postion'
        stoploss_tag = 'Stoploss Postion'
        
        filled_entries = trade.select_filled_orders()
        first_order = filled_entries[0]
        if first_order.order_id != order.order_id: # Not trade's first order 
            
            metadataMap = trade.get_custom_data(key='GRIDMETADATAS')
            
            if increase_tag in order.ft_order_tag: # increase order
                metadataMap['vaildOrderIDs'].append(order.order_id)
                logger.info(f"{increase_tag} append vaildOrderID {order.order_id}")
            elif decrease_tag in order.ft_order_tag: # decrease order
                orderID = metadataMap['vaildOrderIDs'].pop()
                logger.info(f"{decrease_tag} remove vaildOrderID {orderID}")
            elif stoploss_tag in order.ft_order_tag: # stoploss order
                return None
        
            trade.set_custom_data(key='GRIDMETADATAS',value=metadataMap) # store metadataMap
            return None
        
        
        
        # trade's first order
        metadataMap = {}
        metadataMap['smallGrid'] = 0.0
        metadataMap['bigGrid'] = 0.0
        metadataMap['stoplossLine'] = []
        metadataMap['fistOrderPrice'] = 0.0
        metadataMap['vaildOrderIDs'] = []
        
        current_rate = first_order.safe_price
        metadataMap['fistOrderPrice'] = current_rate

        # init small price grid
        metadataMap['smallGrid'] = current_rate*self.smallGridPercent

        # init big price grid
        metadataMap['bigGrid'] = current_rate*self.bigGridPercent

        
        # init line        
        if trade.entry_side == "buy" :
            metadataMap['stoplossLine'] = [current_rate, current_rate-1*metadataMap['bigGrid'], current_rate-5*metadataMap['bigGrid']]
        elif trade.entry_side == "sell" :
            metadataMap['stoplossLine'] = [current_rate, current_rate+1*metadataMap['bigGrid'], current_rate+5*metadataMap['bigGrid']]

        
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
        logger.info(f"read metadata {trade.pair}, metadataMap {metadataMap}")
        
        stoplossLine = metadataMap['stoplossLine']
        if len(stoplossLine) != 3 :
            logger.error(f"{trade.pair} stoplossLine is not 2, stoplossLine: {stoplossLine}")
            return None
        
        fistOrderPrice = metadataMap['fistOrderPrice']
        if fistOrderPrice == 0 :
            logger.error(f"{trade.pair} fistOrderPrice is 0")
            return None

        smallGrid = metadataMap['smallGrid']
        if smallGrid == 0.0 :
            logger.error(f"{trade.pair} smallGrid is 0, smallGrid: {smallGrid}")
            return None
        
        bigGrid = metadataMap['bigGrid']
        if bigGrid == 0.0 :
            logger.error(f"{trade.pair} bigGrid is 0, bigGrid: {bigGrid}")
            return None
        
        vaildOrderIDs = metadataMap['vaildOrderIDs']
        if len(vaildOrderIDs) == 0 :
            logger.error(f"{trade.pair} vaildOrderIDs is 0, vaildOrderIDs: {vaildOrderIDs}")
            return None
        
    # ---------------- GRID stoploss position --------------
        stoplossStartLine = stoplossLine[0]
        stoplossEndLine = stoplossLine[1]
        stoplossTriggerLine = stoplossLine[2]
        if trade.entry_side == 'buy' : 
            if current_rate < stoplossTriggerLine:
                # calculate stoploss amount
                to_stoplossorders = find_valid_buyorders_betweenline(trade, vaildOrderIDs, stoplossStartLine, stoplossEndLine) # stoplossStartLine is upline when long
                to_stoplossamount = 0
                to_stoplossstakeamount = 0
                to_stoplossorderids = []
                for order in to_stoplossorders:
                    to_stoplossamount += order.safe_amount
                    to_stoplossstakeamount += order.safe_cost
                    to_stoplossorderids.append(order.order_id)
                # change line and stackAmount after stoploss
                metadataMap['stoplossLine'] = [stoplossEndLine, stoplossEndLine-1*bigGrid, stoplossEndLine-5*bigGrid]
                logger.info(f"Stoploss Postion remove vaildOrderID {to_stoplossorderids}")
                metadataMap['vaildOrderIDs'] = removeStoplossOrderIDs(metadataMap['vaildOrderIDs'],to_stoplossorderids)
                trade.set_custom_data(key='GRIDMETADATAS',value=metadataMap)
                try:
                    return -to_stoplossamount, f'Stoploss Postion, stoplossamount: {to_stoplossamount} loss: -{to_stoplossstakeamount - to_stoplossamount*current_rate}'
                except Exception as exception:
                    return None
                
                
        if trade.entry_side == 'sell' : 
            if current_rate > stoplossTriggerLine:
                # calculate stoploss amount
                to_stoplossorders = find_valid_sellorders_betweenline(trade, vaildOrderIDs, stoplossStartLine, stoplossEndLine) # stoplossStartLine is downline when short
                to_stoplossamount = 0
                to_stoplossstakeamount = 0
                to_stoplossorderids = []
                for order in to_stoplossorders:
                    to_stoplossamount += order.safe_amount
                    to_stoplossstakeamount += order.safe_cost
                    to_stoplossorderids.append(order.order_id)
                # change line and stackAmount after stoploss
                metadataMap['stoplossLine'] = [stoplossEndLine, stoplossEndLine+1*bigGrid, stoplossEndLine+5*bigGrid]
                logger.info(f"Stoploss Postion remove vaildOrderID {to_stoplossorderids}")
                metadataMap['vaildOrderIDs'] = removeStoplossOrderIDs(metadataMap['vaildOrderIDs'],to_stoplossorderids)
                trade.set_custom_data(key='GRIDMETADATAS',value=metadataMap)
                try:
                    return -to_stoplossamount, f'Stoploss Postion, stoplossamount: {to_stoplossamount} loss: {to_stoplossstakeamount - to_stoplossamount*current_rate}'
                except Exception as exception:
                    return None
                
                
        
    # ---------------- GRID increse position ---------------     
        lastOperateOrder = find_last_operate_order(trade)
        if lastOperateOrder == None:
            logger.error(f"{trade.pair} lastOperateOrder is None")
            return None
        
        filledEntries = trade.select_filled_orders(trade.entry_side)
        firstStakeAmount = filledEntries[0].stake_amount
        
        # long trade increase postion where curPrice <= lastPrice - GridPrice
        if trade.entry_side == 'buy' : 
            if current_rate <= lastOperateOrder.safe_price - smallGrid:
                try:
                    if fistOrderPrice - smallGrid * self.sameStackAmountGirds  < current_rate <= fistOrderPrice :
                        stake_amount = firstStakeAmount
                    elif current_rate <= fistOrderPrice - smallGrid * self.sameStackAmountGirds:
                        priod = ((fistOrderPrice - smallGrid * self.sameStackAmountGirds) - current_rate) / smallGrid 
                        stake_amount = firstStakeAmount + self.stakeAmountPeriod * int(priod)
                    
                    return stake_amount, f'Increase Postion, stake_amount: {stake_amount}, lastorderprice {lastOperateOrder.safe_price}, currentrate: {current_rate}, lines: {lineList}'
                except Exception as exception:
                    return None
                
                
        # short trade increase postion where curPrice >= lastPrice + GridPrice
        if trade.entry_side == 'sell' : 
            if current_rate >= lastOperateOrder.safe_price + smallGrid:
                try:
                    if fistOrderPrice + smallGrid * self.sameStackAmountGirds  > current_rate >= fistOrderPrice :
                        stake_amount = firstStakeAmount
                    elif current_rate >= fistOrderPrice - smallGrid * self.sameStackAmountGirds:
                        priod = (current_rate- (fistOrderPrice - smallGrid * self.sameStackAmountGirds)) / smallGrid 
                        stake_amount = firstStakeAmount + self.stakeAmountPeriod * int(priod)
                        
                    return stake_amount, f'Increase Postion, stake_amount: {stake_amount}, lastorderprice {lastOperateOrder.safe_price}, currentrate: {current_rate}, lines: {lineList}'
                except Exception as exception:
                    return None
        
        
        
    # ---------------- GRID decrease position --------------
        # long trade decrese postion where curPrice >= lastPrice + GridPrice
        lastValidOrder = find_last_valid_order(trade, vaildOrderIDs[-1])
        if lastValidOrder == None:
            logger.error(f"{trade.pair}  validOrders is None")
            return None
        
        if trade.entry_side == 'buy' : 
            if current_rate >= lastValidOrder.safe_price + smallGrid:
                try:
                    return -lastValidOrder.safe_amount, f'Decrease Postion, oppsite order price: {lastValidOrder.safe_price} amount: {lastValidOrder.safe_amount}'
                except Exception as exception:
                    return None
                
                
        # short trade decrease postion where curPrice <= lastPrice - GridPrice
        if trade.entry_side == 'sell' : 
            if current_rate <= lastValidOrder.safe_price - smallGrid:
                try:
                    return -lastValidOrder.safe_amount, f'Decrease Postion, oppsite order price: {lastValidOrder.safe_price} amount: {lastValidOrder.safe_amount}'
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