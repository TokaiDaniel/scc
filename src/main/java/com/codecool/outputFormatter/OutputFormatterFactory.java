package com.codecool.outputFormatter;

import com.codecool.OutputFormat;

public class OutputFormatterFactory {

    public OutputFormatter createByFormat(OutputFormat outputFormat){
        switch (outputFormat){
            case XML -> {
                return new XMLOutputFormatter();
            }
            case JSON -> {
                return new JsonOutputFormatter();
            }
            case TABLE -> {
                return new TableOutputFormatter();
            }
        }
        return null;
    }
}
