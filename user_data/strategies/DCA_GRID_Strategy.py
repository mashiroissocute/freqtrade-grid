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
import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.persistence import Trade, Order
from typing import Optional, Tuple, Union
from freqtrade.strategy import stoploss_from_open

logger = logging.getLogger(__name__)



class DCAGRID(IStrategy):

    INTERFACE_VERSION: int = 3
    can_short = False
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    amend_last_stake_amount = True

    order_types = {
        'entry': 'market',
        'exit': 'market',
        'stoploss': 'market',
        'stoploss_on_exchange': False
    }
    order_time_in_force = {
        'entry': 'GTC',
        'exit': 'GTC'
    }
    timeframe = '1m'
    
    
    minimal_roi = {
        "0": 3
    }
    stoploss =  -1
    trailing_stop = True
    trailing_stop_positive = 0.05
    trailing_stop_positive_offset = 0.25
    trailing_only_offset_is_reached = True

    
    Mode = 'DCA' # 'DCA' or 'GRID'
    
    initStakeAmount = 10
    stakeAmountPeriod = 5
    
    smallGridPercent = 0.01
    bigGridPercent = 0.05


    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe['close'] > 0)
            ),
            'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
            ),
            'exit_long'] = 0
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
        
        return self.initStakeAmount
    
    
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
            logger.info(f"after order_filled metadata {trade.pair}, metadataMap {metadataMap}")
            return None
        
        # trade's first order
        metadataMap = {}
        metadataMap['smallGrid'] = 0.0
        metadataMap['bigGrid'] = 0.0
        metadataMap['lineList'] = []
        metadataMap['fistOrderPrice'] = 0.0
        metadataMap['vaildOrderIDs'] = []
        
        current_rate = first_order.safe_price
        # init first order price 
        metadataMap['fistOrderPrice'] = current_rate
        # init small price grid
        metadataMap['smallGrid'] = current_rate*self.smallGridPercent
        # init big price grid
        metadataMap['bigGrid'] = current_rate*self.bigGridPercent
        # init orderIDS
        metadataMap['vaildOrderIDs'] = [order.order_id]
        logger.info(f"init metadata, metadataMap {metadataMap}")
        trade.set_custom_data(key='GRIDMETADATAS',value=metadataMap)
        return None

    
    # GRID / DCA ORDERs
    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: Optional[float], max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs
                              ) -> Union[Optional[float], Tuple[Optional[float], Optional[str]]]:
    # --------------- read and check metadata -----------------
        metadataMap = trade.get_custom_data(key='GRIDMETADATAS')
        
        firstOrderPrice = metadataMap['fistOrderPrice']
        if firstOrderPrice == 0.0 :
            logger.error(f"{trade.pair} firstOrderPrice is 0, firstOrderPrice: {firstOrderPrice}")
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
                          
    # ---------------- DCA / GRID increse position ---------------   
        
        lastOperateOrder = find_last_operate_order(trade)
        if lastOperateOrder == None:
            logger.error(f"{trade.pair} lastOperateOrder is None")
            return None
        
        # long trade increase postion where curPrice <= lastPrice - GridPrice
        if trade.entry_side == 'buy' : 
            if current_rate <= lastOperateOrder.safe_price - smallGrid:
                try:
                    priod = (firstOrderPrice - current_rate) / bigGrid 
                    stake_amount = self.initStakeAmount + self.stakeAmountPeriod * int(priod)
                    return stake_amount, f'Increase Postion, stake_amount: {stake_amount}, lastorderprice {lastOperateOrder.safe_price}, currentrate: {current_rate}, lines: {lineList}'
                except Exception as exception:
                    return None
                
        
    # ---------------- DCA / GRID decrease position --------------
    
        # long trade decrese postion where curPrice >= lastPrice + GridPrice
        lastValidOrder = find_last_valid_order(trade, vaildOrderIDs[-1])
        if lastValidOrder == None:
            logger.error(f"{trade.pair}  validOrders is None")
            return None
        
        if self.Mode == 'DCA':
            return None # DCA Mode use ROI to exit trade
        elif self.Mode == 'GRID':
            if trade.entry_side == 'buy' : 
                if current_rate >= lastValidOrder.safe_price + smallGrid:
                    try:
                        return -lastValidOrder.safe_amount, f'Decrease Postion, oppsite order price: {lastValidOrder.safe_price} amount: {lastValidOrder.safe_amount}'
                    except Exception as exception:
                        return None
                
        return None



def find_last_operate_order(trade: Trade):
    stop_loss_tag = 'Stoploss Postion'
    filled_entries = trade.select_filled_orders()
    for order in reversed(filled_entries):
        if stop_loss_tag not in order.ft_order_tag: # exclude stoploss order
            return order
    return None

def find_last_valid_order(trade: Trade, orderID: str):   
    filled_entries = trade.select_filled_orders()
    for order in reversed(filled_entries):
        if orderID == order.order_id: # match orderid 
            return order
    return None

