package com.codecool.outputFormatter;

import java.util.ArrayList;

public class JsonOutputFormatter implements OutputFormatter{
    @Override
    public void printToConsole(ArrayList<String> data) {
        System.out.println("JSONOutput");
    }
}
