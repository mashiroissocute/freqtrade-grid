#!/bin/bash

start_date="20240707"
end_date="20240708"
output_file="result_backtesing_shell_1min.txt"

strategy="GRIDDMIPRICEStrategyFutureV4"

timeframe="4h"
bigtimeframe="4h"
mode="user_data/config_future_test_GRID.json"



# freqtrade download-data --config $mode  --timerange $start_date-$end_date --timeframe $timeframe $bigtimeframe
freqtrade backtesting --config $mode --strategy-list $strategy --timerange $start_date-$end_date --timeframe $timeframe >> $output_file
