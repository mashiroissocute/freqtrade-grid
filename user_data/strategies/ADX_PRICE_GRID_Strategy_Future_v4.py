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



class GRIDDMIPRICEStrategyFutureV4(IStrategy):

    INTERFACE_VERSION: int = 3
    can_short = False
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    amend_last_stake_amount = True

    minimal_roi = {
        "0": 1
    }
    
    stoploss =  -1    

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

    adxWindow = IntParameter(7, 21, default=12, space="buy")
    adxThr = IntParameter(15, 35, default=25, space="buy")
    emaThrLong = IntParameter(5, 55, default=24, space="buy")
    emaThrShort = IntParameter(5, 55, default=24, space="buy")
    
    initStakeAmount = 10
    stakeAmountPeriod = 10
    
    smallGridPercent = 0.01 
    bigGridPercent = 0.02
    smallGridMap = {} # store pair smallGrid (price)
    bigGridMap = {} # store pair bigGrid (price)
    
    stakeAmountMap = {} # store pair stakeamounts
    lineMap = {} # store pair lines


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
    
    # the initial order (opening trade)
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        
        
        logger.info(f"custom_stake_amount, pair:{pair}")
        # init stakeAmount
        if pair in self.stakeAmountMap:
            del self.stakeAmountMap[pair]
            
        self.stakeAmountMap[pair] = [self.initStakeAmount, self.initStakeAmount+1*self.stakeAmountPeriod, self.initStakeAmount+2*self.stakeAmountPeriod]
        logger.info(f"custom_stake_amount, stakeAmountMap:{self.stakeAmountMap}")

        # init small price grid
        if pair in self.smallGridMap:
            del self.smallGridMap[pair]
        
        self.smallGridMap[pair] = current_rate*self.smallGridPercent
        logger.info(f"custom_stake_amount, smallGridMap:{self.smallGridMap}")
        
        # init big price grid
        if pair in self.smallGridMap:
            del self.smallGridMap[pair]
            
        self.bigGridMap[pair] = current_rate*self.bigGridPercent
        logger.info(f"custom_stake_amount, bigGridMap:{self.bigGridMap}")
        
            
        # init line
        if pair in self.lineMap:
            del self.lineMap[pair]
        
        
        if side == "long" :
            self.lineMap[pair] = [current_rate, current_rate-1*self.bigGridMap[pair],current_rate-2*self.bigGridMap[pair],current_rate-3*self.bigGridMap[pair]]
            
        if side == "short" :
            self.lineMap[pair] = [current_rate, current_rate+1*self.bigGridMap[pair],current_rate+2*self.bigGridMap[pair],current_rate+3*self.bigGridMap[pair]]
        
        logger.info(f"custom_stake_amount, lineMap:{self.lineMap}")

        
        return self.stakeAmountMap[pair][0]
    

    
    # GRID ORDERs
    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: Optional[float], max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs
                              ) -> Union[Optional[float], Tuple[Optional[float], Optional[str]]]:
        logger.info(f"adjust_trade_position, smallGridMap:{self.smallGridMap}")
        
    # ---------------- GRID stoploss position --------------
        lines = self.lineMap[trade.pair]
        if len(lines) != 4 :
            logger.error(f"{trade.pair} lineMap is not 4, lineMap: {lines}")
            return None
        
        
        stoplossStartLine = lines[0] # stoploss start line
        stoplossEndLine = lines[1] # stoploss end line
        stoplossTriggerLine = lines[3] # stoploss trigger line
        
        if trade.entry_side == 'buy' : 
            if current_rate < stoplossTriggerLine:
                to_stoplossorders = find_vaild_orders_betweenline(trade, stoplossStartLine, stoplossEndLine) # stoplossStartLine > stoplossEndLine when long
                to_stoplossamount = 0
                to_stoplossstakeamount = 0
                for order in to_stoplossorders:
                    to_stoplossamount += order.safe_amount
                    to_stoplossstakeamount += order.safe_cost
                # change line and stackAmount after stoploss
                self.lineMap[trade.pair] = [stoplossStartLine, stoplossStartLine-1*self.bigGridMap[trade.pair],stoplossStartLine-2*self.bigGridMap[trade.pair],stoplossStartLine-3*self.bigGridMap[trade.pair]]
                initStakeAmount = self.stakeAmountMap[trade.pair][1]
                self.stakeAmountMap[trade.pair] = [initStakeAmount, initStakeAmount+1*self.stakeAmountPeriod, initStakeAmount+2*self.stakeAmountPeriod]
                try:
                    return -to_stoplossamount, f'Stoploss Postion, currentrate: {current_rate}, beforeLines: {lines}, currentLines:{self.lineMap[trade.pair]}, amount: {to_stoplossamount} loss: -{to_stoplossstakeamount - to_stoplossamount*current_rate}'
                except Exception as exception:
                    return None
                
                
        if trade.entry_side == 'sell' : 
            if current_rate > stoplossTriggerLine:
                to_stoplossorders = find_vaild_orders_betweenline(trade, stoplossStartLine, stoplossEndLine) # stoplossEndLine > stoplossStartLine  when short
                to_stoplossamount = 0
                for order in to_stoplossorders:
                    to_stoplossamount += order.safe_amount
                    to_stoplossstakeamount += order.safe_cost
                # change line and stackAmount after stoploss
                self.lineMap[trade.pair] = [stoplossStartLine, stoplossStartLine+1*self.bigGridMap[trade.pair],stoplossStartLine+2*self.bigGridMap[trade.pair],stoplossStartLine+3*self.bigGridMap[trade.pair]]
                initStakeAmount = self.stakeAmountMap[trade.pair][1]
                self.stakeAmountMap[trade.pair] = [initStakeAmount, initStakeAmount+1*self.stakeAmountPeriod, initStakeAmount+2*self.stakeAmountPeriod]
                try:
                    return -to_stoplossamount, f'Stoploss Postion, currentrate: {current_rate}, beforeLines: {lines}, currentLines:{self.lineMap[trade.pair]}, amount: {to_stoplossamount} loss: -{to_stoplossamount*current_rate - to_stoplossstakeamount}'
                except Exception as exception:
                    return None
                
                
        
    # ---------------- GRID increse position ---------------   
        stakeAmounts = self.stakeAmountMap[trade.pair]
        if len(stakeAmounts) != 3 :
            logger.error(f"{trade.pair} stakeAmountMap is not 3, stakeAmounts: {stakeAmounts}")
            return None
        
        line0 = lines[0] 
        line1 = lines[1]
        line2 = lines[2]
        line3 = lines[3]
        stakeAmount1 = stakeAmounts[0] # between line0 - line1
        stakeAmount2 = stakeAmounts[1] # between line1 - line2
        stakeAmount3 = stakeAmounts[2] # between line2 - line3
        
        lastOperateOrder = find_last_orders(trade)
        if lastOperateOrder == None:
            logger.error(f"{trade.pair} lastOperateOrder is None")
            return None
        
        # long trade increase postion where curPrice <= lastPrice - GridPrice
        if trade.entry_side == 'buy' : 
            if current_rate <= lastOperateOrder.safe_price - self.smallGridMap[trade.pair]:
                try:
                    if line1 <=current_rate < line0:
                        stake_amount = stakeAmount1
                    elif line2 <=current_rate < line1:
                        stake_amount = stakeAmount2
                    elif line3 <=current_rate < line2:
                        stake_amount = stakeAmount3
                        
                    return stake_amount, f'Increase Postion, stake_amount: {stake_amount}, lastorderprice {lastValidOrder.safe_price}, currentrate: {current_rate}, lines: {lines}'
                except Exception as exception:
                    return None
                
                
        # short trade increase postion where curPrice >= lastPrice + GridPrice
        if trade.entry_side == 'sell' : 
            if current_rate >= lastOperateOrder.safe_price + self.smallGridMap[trade.pair]:
                try:
                    if line1 >=current_rate > line0:
                        stake_amount = stakeAmount1
                    elif line2 >=current_rate > line1:
                        stake_amount = stakeAmount2
                    elif line3 >=current_rate > line2:
                        stake_amount = stakeAmount3
                        
                    return stake_amount, f'Increase Postion, stake_amount: {stake_amount}, lastorderprice {lastValidOrder.safe_price}, currentrate: {current_rate}, lines: {lines}'
                except Exception as exception:
                    return None
        
        
        
    # ---------------- GRID decrease position --------------
        validOrders = find_vaild_orders_betweenline(trade,line0,line3)
        if len(validOrders) == 0:
            logger.error(f"{trade.pair} validOrders is None")
            return None
        
        lastValidOrder = validOrders[-1]

        # long trade decrese postion where curPrice >= lastPrice + GridPrice
        if trade.entry_side == 'buy' : 
            if current_rate >= lastValidOrder.safe_price + self.smallGridMap[trade.pair]:
                try:
                    return -lastValidOrder.safe_amount, f'Decrese Postion, oppsite order price: {lastValidOrder.safe_price} amount: {lastValidOrder.safe_amount}'
                except Exception as exception:
                    return None
                
                
        # short trade decrease postion where curPrice <= lastPrice - GridPrice
        if trade.entry_side == 'sell' : 
            if current_rate <= lastValidOrder.safe_price - self.smallGridMap[trade.pair]:
                try:
                    return -lastValidOrder.safe_amount, f'Decrese Postion, oppsite order price: {lastValidOrder.safe_price} amount: {lastValidOrder.safe_amount}'
                except Exception as exception:
                    return None
                
        
        return None
    
        
    
def find_last_orders(trade: Trade) -> Order:
    stop_loss_tag = 'Stoploss Postion'
        
    
    filled_entries = trade.select_filled_orders()
    
    for order in reversed(filled_entries):
        if stop_loss_tag not in order.ft_order_tag: # exclude stoploss order
            return order
                
    return None


def find_vaild_orders_betweenline(trade: Trade, lineUp: float, lineDown: float) -> List[Order]:    
    if lineUp <= lineDown: # make sure lineUp > lineDown
        temp = lineUp
        lineUp = lineDown
        lineDown = temp
    
    
    stop_loss_tag = 'Stoploss Postion'
    
    vaild_orders: List[Order] = []
    
    filled_entries = trade.select_filled_orders()
    init_order_side = trade.entry_side
    
    for order in filled_entries:
        if stop_loss_tag not in order.ft_order_tag: # exclude stoploss order
            if lineDown <= order.safe_price <= lineUp: # between
                if order.ft_order_side == init_order_side :
                    vaild_orders.append(order)
                else:
                    vaild_orders.pop()
    return vaild_orders
