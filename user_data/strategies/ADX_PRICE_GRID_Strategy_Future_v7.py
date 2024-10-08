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



class GRIDDMIPRICEStrategyFutureV7(IStrategy):

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

    
    initStakeAmount = 10 # init stake amount 
    stakeAmountPeriod = 1 # increase amount after sameStackAmountGirds
    sameStackAmountGirds = 5 # 20 * 0.006 = 0.12 add stackamount after 12%
    
    smallGridPercent = 0.001
    bigGridPercent = 0.01 # used in stoploss , stoploss after 30%   10 * bigGridPercent


    windowSize = 30 # Min Max price windown
    
    Z = 1 
    LowRatio = 1
    HighRatio = 0.3
    

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
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe=self.inf_tf)
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
                (dataframe['close'] > 0)
            ),
            'enter_long'] = 1
        
        dataframe.loc[
            (
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
        return 10
    
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
        
        k = (self.LowRatio - self.HighRatio) / (lowestPrice - highestPrice)
        b = (self.HighRatio*lowestPrice - self.LowRatio*highestPrice) / (lowestPrice - highestPrice)
           
        ratio = k*current_rate + b
        
        logger.info(f"{pair} highest-{highestPrice} lowest-{lowestPrice} current-{current_rate} ratio-{ratio}")
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
                logger.info(f"{pair} {increase_tag} append vaildOrderID {order.order_id}")
            elif decrease_tag in order.ft_order_tag: # decrease order
                orderID = metadataMap['vaildOrderIDs'].pop()
                logger.info(f"{pair} {decrease_tag} remove vaildOrderID {orderID}")
            elif stoploss_tag in order.ft_order_tag: # stoploss order
                logger.info(f"after order_filled metadata {trade.pair}, metadataMap {metadataMap}")
                return None
        
            trade.set_custom_data(key='GRIDMETADATAS',value=metadataMap) # store metadataMap
            logger.info(f"after order_filled metadata {trade.pair}, metadataMap {metadataMap}")
            return None
        
        
        
        # trade's first order
        metadataMap = {}
        metadataMap['smallGrid'] = 0.0
        metadataMap['bigGrid'] = 0.0
        metadataMap['stoplossLine'] = []
        metadataMap['fistOrderPrice'] = 0.0
        metadataMap['fistOrderStake'] = 0.0
        metadataMap['vaildOrderIDs'] = []
        
        
        current_rate = first_order.safe_price
        current_stake = first_order.stake_amount
        metadataMap['fistOrderPrice'] = current_rate
        metadataMap['fistOrderStake'] = current_stake
       
        # init small price grid
        metadataMap['smallGrid'] = current_rate*self.smallGridPercent

        
        # init big price grid
        metadataMap['bigGrid'] = current_rate*self.bigGridPercent

        
        # init line        
        if trade.entry_side == "buy" :
            metadataMap['stoplossLine'] = [current_rate, current_rate-1*metadataMap['bigGrid'], current_rate-10*metadataMap['bigGrid']]
        elif trade.entry_side == "sell" :
            metadataMap['stoplossLine'] = [current_rate, current_rate+1*metadataMap['bigGrid'], current_rate+10*metadataMap['bigGrid']]

        
        
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
        # logger.info(f"read metadata {trade.pair}, metadataMap {metadataMap}")
        
        stoplossLine = metadataMap['stoplossLine']
        if len(stoplossLine) != 3 :
            logger.error(f"{trade.pair} stoplossLine is not 3, stoplossLine: {stoplossLine}")
            return None
        
        fistOrderPrice = metadataMap['fistOrderPrice']
        if fistOrderPrice == 0 :
            logger.error(f"{trade.pair} fistOrderPrice is 0")
            return None
        
        fistOrderStake = metadataMap['fistOrderStake']
        if fistOrderStake == 0 :
            logger.error(f"{trade.pair} fistOrderStake is 0")
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
        stoplossStartLine = stoplossLine[0] # stoploss start line
        stoplossEndLine = stoplossLine[1] # stoploss end line
        stoplossTriggerLine = stoplossLine[2] # stoploss trigger line
        
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
                # change stoplossLine and validOrderIDs after stoploss
                metadataMap['stoplossLine'] = [stoplossEndLine, stoplossEndLine-1*bigGrid, stoplossEndLine-10*bigGrid]
                metadataMap['vaildOrderIDs'] = removeStoplossOrderIDs(metadataMap['vaildOrderIDs'],to_stoplossorderids)
                trade.set_custom_data(key='GRIDMETADATAS',value=metadataMap)
                logger.info(f"{trade.pair} Stoploss Postion remove vaildOrderIDs {to_stoplossorderids}")
                try:
                    return -to_stoplossamount, f'Stoploss Postion, stoplossamount: {to_stoplossamount} loss: -{to_stoplossstakeamount - to_stoplossamount*current_rate}'
                except Exception as exception:
                    return None
                
        
    # ---------------- GRID increse position ---------------   
        lastOperateOrder = find_last_operate_order(trade)
        if lastOperateOrder == None:
            logger.error(f"{trade.pair} lastOperateOrder is None")
            return None
        firstStakeAmount = fistOrderStake
        increaseStackAmountRation = firstStakeAmount / self.initStakeAmount
        # long trade increase postion where curPrice <= lastPrice - GridPrice
        if trade.entry_side == 'buy' : 
            sameStakAmountLine = fistOrderPrice - smallGrid * self.sameStackAmountGirds
            if current_rate <= lastOperateOrder.safe_price - smallGrid:
                try:
                    if sameStakAmountLine < current_rate <= fistOrderPrice :
                        stake_amount = firstStakeAmount
                    elif current_rate <= sameStakAmountLine:
                        priod = (sameStakAmountLine - current_rate) / smallGrid 
                        stake_amount = firstStakeAmount + self.stakeAmountPeriod * int(priod) * increaseStackAmountRation
                    logger.info(f'{trade.pair} Increase Postion, stake_amount: {stake_amount}, lastorderprice {lastOperateOrder.safe_price}, currentrate: {current_rate}')
                    return stake_amount, f'Increase Postion, stake_amount: {stake_amount}, lastorderprice {lastOperateOrder.safe_price}, currentrate: {current_rate}'
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
                    logger.info(f'{trade.pair} Decrease Postion, amount: {lastValidOrder.safe_amount}, oppsiteorderprice {lastValidOrder.safe_price}, currentrate: {current_rate}')
                    return -lastValidOrder.safe_amount, f' Decrease Postion, amount: {lastValidOrder.safe_amount}, oppsiteorderprice {lastValidOrder.safe_price}, currentrate: {current_rate}'
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