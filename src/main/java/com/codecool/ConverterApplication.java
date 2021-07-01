package com.codecool;

import java.io.File;

public class ConverterApplication {
    public static void main(String[] args) {
        File file;
        OutputFormat outputFormat;

        if (args.length == 0) {
            System.out.println("No input file defined");
        }
        else if (args.length == 1){
            outputFormat = OutputFormat.TABLE;
            file = new File(args[1]);
        }
        else if (args.length == 2){
            if (args[0].equals("JSON")){
                outputFormat = OutputFormat.JSON;
            }
            else if(args[0].equals("XML")){
                outputFormat = OutputFormat.XML;
            }
            file = new File(args[1]);
        }
    }
}
