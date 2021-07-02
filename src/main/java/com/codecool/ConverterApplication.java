package com.codecool;

import java.io.File;
import java.io.IOException;

public class ConverterApplication {
    public static void main(String[] args) throws IOException {
        File file = null;
        OutputFormat outputFormat = OutputFormat.TABLE;

        if (args.length == 0) {
            System.out.println("No input file defined");
            System.exit(0);
        }
        else if (args.length == 1){
            file = new File(args[0]);
        }
        else if (args.length == 2){
            if (args[0].equals("JSON")){
                outputFormat = OutputFormat.JSON;
            }
            else if(args[0].equals("xml")){
                outputFormat = OutputFormat.XML;
            }
            file = new File(args[1]);
        }

        SimpleCsvConverter simpleCsvConverter = new SimpleCsvConverter(file, outputFormat );
        simpleCsvConverter.convert();
    }
}
